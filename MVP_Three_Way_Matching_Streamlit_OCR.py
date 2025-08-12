# app.py
"""
Three-Way Matching — Robust OCR + Safe Regex (Streamlit)
Usage: deploy ke Streamlit Community Cloud (share.streamlit.io) atau run locally.
"""

import streamlit as st
import pdfplumber
import docx
import re
import io
from PIL import Image
import pytesseract
import dateparser
from datetime import datetime
import pandas as pd
import tempfile
import math

st.set_page_config(page_title="Three-Way Matching (OCR Safe)", layout="wide")

# -------------------- Helpers --------------------

def clean_text(txt: str) -> str:
    if not txt:
        return ""
    txt = txt.replace("\x00", " ")
    txt = re.sub(r"[\u0000-\u001F\u007F-\u009F]+", " ", txt)
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()

def ocr_pil_image(pil_img, ocr_lang="ind+eng"):
    try:
        return pytesseract.image_to_string(pil_img, lang=ocr_lang)
    except Exception:
        try:
            # fallback basic
            return pytesseract.image_to_string(pil_img)
        except Exception:
            return ""

def extract_text_from_pdf_bytes(file_bytes, ocr_lang="ind+eng", max_pages=None):
    text = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        pages = pdf.pages
        if max_pages:
            pages = pages[:max_pages]
        for p in pages:
            page_text = p.extract_text() or ""
            if len(page_text.strip()) < 15:
                # perform OCR on page image (pdfplumber gives .to_image())
                try:
                    img = p.to_image(resolution=300).original  # PIL Image
                    ocr_text = ocr_pil_image(img, ocr_lang=ocr_lang)
                    page_text = ocr_text
                except Exception:
                    page_text = ""
            text += "\n" + page_text
    return clean_text(text)

def extract_text_from_docx_bytes(file_bytes):
    try:
        doc = docx.Document(io.BytesIO(file_bytes))
        full = [p.text for p in doc.paragraphs]
        return clean_text("\n".join(full))
    except Exception:
        return ""

def extract_text_from_image_bytes(file_bytes, ocr_lang="ind+eng"):
    try:
        img = Image.open(io.BytesIO(file_bytes))
        return clean_text(ocr_pil_image(img, ocr_lang=ocr_lang))
    except Exception:
        return ""

def extract_text_from_upload(uploaded_file, ocr_lang="ind+eng", max_pages=None):
    if uploaded_file is None:
        return ""
    name = uploaded_file.name.lower()
    data = uploaded_file.read()
    uploaded_file.seek(0)
    if name.endswith(".pdf"):
        return extract_text_from_pdf_bytes(data, ocr_lang=ocr_lang, max_pages=max_pages)
    if name.endswith((".docx", ".doc")):
        return extract_text_from_docx_bytes(data)
    if name.endswith((".png", ".jpg", ".jpeg", ".tiff", ".bmp")):
        return extract_text_from_image_bytes(data, ocr_lang=ocr_lang)
    # fallback decode
    try:
        return clean_text(data.decode("utf-8", errors="ignore"))
    except Exception:
        return ""

def safe_search(pattern, text, flags=re.IGNORECASE):
    if not pattern or not text:
        return None
    try:
        m = re.search(pattern, text, flags)
        if m:
            return m.group(1).strip()
    except re.error:
        return None
    return None

def normalize_amount(s):
    if s is None:
        return None
    s = str(s)
    s = s.replace("Rp", "").replace("IDR", "")
    s = re.sub(r"[^0-9,\.]", "", s)
    # handle thousand/decimal separators
    if s.count(",") == 1 and s.count(".") > 1:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except Exception:
        return None

def parse_date_flexible(text):
    if not text:
        return None
    # try to parse with dateparser; returns datetime or None
    d = dateparser.parse(text, languages=["id", "en"])
    return d

# global date regex tries many month names + numeric forms
DATE_PATTERN_GLOBAL = r"(\d{1,2}\s*(?:Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s*\d{2,4})"
NUMERIC_DATE_PATTERN = r"(\d{1,2}[\/\-\.\s]\d{1,2}[\/\-\.\s]\d{2,4})"

def extract_any_date(text):
    if not text:
        return None
    # try global textual months
    m = re.search(DATE_PATTERN_GLOBAL, text, flags=re.IGNORECASE)
    if m:
        parsed = parse_date_flexible(m.group(1))
        if parsed:
            return m.group(1), parsed
    # try numeric
    m2 = re.search(NUMERIC_DATE_PATTERN, text)
    if m2:
        parsed = parse_date_flexible(m2.group(1))
        if parsed:
            return m2.group(1), parsed
    # fallback: try to find any date-like token and parse
    tokens = re.findall(r"\d{1,2}\s+\w+\s+\d{2,4}", text)
    for t in tokens:
        parsed = parse_date_flexible(t)
        if parsed:
            return t, parsed
    return None

# -------------------- Extraction patterns --------------------
# keep patterns simple and safe; we will fallback to extract_any_date if needed
CONTRACT_NUMBER_PATTERNS = [
    r"Nomor\s*Kontrak[:\s\-]*([A-Za-z0-9\-/]+)",
    r"No\.\s*Kontrak[:\s\-]*([A-Za-z0-9\-/]+)",
]
CONTRACT_START_PATTERNS = [
    r"Tanggal\s*Mulai[:\s\-]*([\w\d\s\.,/-]+)",
    r"Mulai[:\s\-]*([\w\d\s\.,/-]+)",
]
CONTRACT_END_PATTERNS = [
    r"Tanggal\s*(?:Selesai|Berakhir)[:\s\-]*([\w\d\s\.,/-]+)",
    r"Selesai[:\s\-]*([\w\d\s\.,/-]+)",
]
CONTRACT_VALUE_PATTERNS = [
    r"Nilai\s*Pekerjaan[:\s\-]*Rp[\s]*([\d\.,]+)",
    r"Total\s*Biaya[:\s\-]*Rp[\s]*([\d\.,]+)",
    r"Total\s*Nilai[:\s\-]*Rp[\s]*([\d\.,]+)",
]

BA_DATE_PATTERNS = [
    r"Tanggal\s*Berita\s*Acara[:\s\-]*([\w\d\s\.,/-]+)",
    r"Tanggal\s*BA[:\s\-]*([\w\d\s\.,/-]+)",
    r"Tanggal[:\s\-]*([\w\d\s\.,/-]+)",
]

INVOICE_DATE_PATTERNS = [
    r"Tanggal\s*Invoice[:\s\-]*([\w\d\s\.,/-]+)",
    r"Tanggal\s*Faktur[:\s\-]*([\w\d\s\.,/-]+)",
    r"Tanggal[:\s\-]*([\w\d\s\.,/-]+)",
]
INVOICE_VALUE_PATTERNS = [
    r"Total\s*Invoice[:\s\-]*Rp[\s]*([\d\.,]+)",
    r"Jumlah[:\s\-]*Rp[\s]*([\d\.,]+)",
    r"Total[:\s\-]*Rp[\s]*([\d\.,]+)",
]

# -------------------- Extractors --------------------

def extract_contract(text):
    res = {}
    text = clean_text(text)
    # nomor
    for p in CONTRACT_NUMBER_PATTERNS:
        v = safe_search(p, text)
        if v:
            res["nomor_kontrak"] = v
            break
    # start
    for p in CONTRACT_START_PATTERNS:
        v = safe_search(p, text)
        if v:
            res["tanggal_mulai_raw"] = v
            res["tanggal_mulai"] = parse_date_flexible(v)
            break
    # end
    for p in CONTRACT_END_PATTERNS:
        v = safe_search(p, text)
        if v:
            res["tanggal_selesai_raw"] = v
            res["tanggal_selesai"] = parse_date_flexible(v)
            break
    # nilai
    for p in CONTRACT_VALUE_PATTERNS:
        v = safe_search(p, text)
        if v:
            res["nilai_kontrak_raw"] = v
            res["nilai_kontrak"] = normalize_amount(v)
            break
    # fallback: any date(s)
    if not res.get("tanggal_mulai") or not res.get("tanggal_selesai"):
        d = extract_any_date(text)
        if d:
            date_str, parsed = d
            if not res.get("tanggal_mulai"):
                res["tanggal_mulai_raw"] = date_str
                res["tanggal_mulai"] = parsed
            elif not res.get("tanggal_selesai"):
                res["tanggal_selesai_raw"] = date_str
                res["tanggal_selesai"] = parsed
    return res

def extract_ba(text):
    res = {}
    text = clean_text(text)
    for p in BA_DATE_PATTERNS:
        v = safe_search(p, text)
        if v:
            res["tanggal_ba_raw"] = v
            res["tanggal_ba"] = parse_date_flexible(v)
            break
    if not res.get("tanggal_ba"):
        d = extract_any_date(text)
        if d:
            res["tanggal_ba_raw"], res["tanggal_ba"] = d[0], d[1]
    return res

def extract_invoice(text):
    res = {}
    text = clean_text(text)
    for p in INVOICE_DATE_PATTERNS:
        v = safe_search(p, text)
        if v:
            res["tanggal_invoice_raw"] = v
            res["tanggal_invoice"] = parse_date_flexible(v)
            break
    for p in INVOICE_VALUE_PATTERNS:
        v = safe_search(p, text)
        if v:
            res["total_raw"] = v
            res["total"] = normalize_amount(v)
            break
    if not res.get("tanggal_invoice"):
        d = extract_any_date(text)
        if d:
            res["tanggal_invoice_raw"], res["tanggal_invoice"] = d[0], d[1]
    return res

# -------------------- UI --------------------

st.title("Three-Way Matching — OCR Safe (MVP)")
st.write("Upload Kontrak (PDF/DOCX), Berita Acara (PDF/DOCX), dan Invoice (PDF/DOCX). App akan extract + validate.")

with st.sidebar:
    st.header("OCR & Parsing")
    ocr_lang = st.selectbox("OCR language", ["ind+eng", "ind", "eng"], index=0)
    max_pages = st.number_input("Max pages to OCR (0 = all)", min_value=0, max_value=100, value=0)
    amount_tolerance_pct = st.number_input("Tolerance for amount match (%)", min_value=0.0, max_value=100.0, value=0.5)

col1, col2 = st.columns([1,2])

with col1:
    st.subheader("1) Upload Kontrak")
    kontrak_file = st.file_uploader("File kontrak (PDF / DOCX / image)", type=["pdf","docx","png","jpg","jpeg","tiff"])
    if st.button("Clear kontrak"):
        st.session_state.pop("kontrak", None)
with col2:
    st.subheader("Hasil ekstraksi kontrak")
    if "kontrak" in st.session_state:
        st.json(st.session_state["kontrak"])
    else:
        st.write("Belum ada kontrak")

if kontrak_file:
    # read bytes then pass to extractor
    bytes_data = kontrak_file.read()
    kontrak_text = extract_text_from_upload(io.BytesIO(bytes_data) if False else kontrak_file, ocr_lang=ocr_lang, max_pages=(None if max_pages==0 else max_pages))
    # note: extract_text_from_upload expects a file-like; earlier we used uploaded_file directly.
    # To avoid complicated re-reads, simpler: call extract_text_from_upload with uploaded_file directly:
    kontrak_text = extract_text_from_upload(kontrak_file, ocr_lang=ocr_lang, max_pages=(None if max_pages==0 else max_pages))
    kontrak = extract_contract(kontrak_text)
    st.session_state["kontrak"] = kontrak
    st.experimental_rerun()

st.markdown("---")

col3, col4 = st.columns([1,2])
with col3:
    st.subheader("2) Upload Berita Acara (BA)")
    ba_file = st.file_uploader("File BA (PDF / DOCX / image)", type=["pdf","docx","png","jpg","jpeg","tiff"])
    if st.button("Clear BA"):
        st.session_state.pop("ba", None)
with col4:
    st.subheader("Hasil ekstraksi BA")
    if "ba" in st.session_state:
        st.json(st.session_state["ba"])
    else:
        st.write("Belum ada BA")

if ba_file:
    ba_text = extract_text_from_upload(ba_file, ocr_lang=ocr_lang, max_pages=(None if max_pages==0 else max_pages))
    ba = extract_ba(ba_text)
    # validate against kontrak dates if present
    if "kontrak" in st.session_state:
        k = st.session_state["kontrak"]
        try:
            if k.get("tanggal_mulai") and k.get("tanggal_selesai") and ba.get("tanggal_ba"):
                start = k["tanggal_mulai"] if isinstance(k["tanggal_mulai"], datetime) else parse_date_flexible(k.get("tanggal_mulai_raw") or k.get("tanggal_mulai"))
                end = k["tanggal_selesai"] if isinstance(k["tanggal_selesai"], datetime) else parse_date_flexible(k.get("tanggal_selesai_raw") or k.get("tanggal_selesai"))
                tba = ba.get("tanggal_ba")
                if isinstance(tba, str):
                    tba = parse_date_flexible(tba)
                if start and end and tba:
                    ba["in_contract_period"] = (start.date() <= tba.date() <= end.date())
                    ba["status"] = "MATCH" if ba["in_contract_period"] else "NOT MATCH"
        except Exception:
            pass
    st.session_state["ba"] = ba
    st.experimental_rerun()

st.markdown("---")

col5, col6 = st.columns([1,2])
with col5:
    st.subheader("3) Upload Invoice")
    invoice_file = st.file_uploader("File Invoice (PDF / DOCX / image)", type=["pdf","docx","png","jpg","jpeg","tiff"])
    if st.button("Clear Invoice"):
        st.session_state.pop("invoice", None)
with col6:
    st.subheader("Hasil ekstraksi Invoice")
    if "invoice" in st.session_state:
        st.json(st.session_state["invoice"])
    else:
        st.write("Belum ada invoice")

if invoice_file:
    invoice_text = extract_text_from_upload(invoice_file, ocr_lang=ocr_lang, max_pages=(None if max_pages==0 else max_pages))
    invoice = extract_invoice(invoice_text)
    # validate date vs BA
    if "ba" in st.session_state and st.session_state["ba"].get("tanggal_ba") and invoice.get("tanggal_invoice"):
        try:
            tba = st.session_state["ba"].get("tanggal_ba")
            if isinstance(tba, str):
                tba = parse_date_flexible(tba)
            tinv = invoice.get("tanggal_invoice")
            if isinstance(tinv, str):
                tinv = parse_date_flexible(tinv)
            if tba and tinv:
                invoice["after_ba"] = tinv.date() >= tba.date()
                invoice["date_status"] = "MATCH" if invoice["after_ba"] else "NOT MATCH"
        except Exception:
            pass
    # validate amount vs kontrak
    if "kontrak" in st.session_state and st.session_state["kontrak"].get("nilai_kontrak") and invoice.get("total") is not None:
        try:
            contract_amt = st.session_state["kontrak"].get("nilai_kontrak")
            inv_amt = invoice.get("total") or invoice.get("dpp") or 0
            if inv_amt is not None and contract_amt is not None:
                tol = float(amount_tolerance_pct) / 100.0
                invoice["amount_match"] = abs(inv_amt - contract_amt) <= (tol * contract_amt)
                invoice["amount_status"] = "MATCH" if invoice["amount_match"] else "NOT MATCH"
        except Exception:
            pass
    st.session_state["invoice"] = invoice
    st.experimental_rerun()

st.markdown("---")

st.header("Ringkasan hasil")
rows = []
k = st.session_state.get("kontrak", {})
b = st.session_state.get("ba", {})
inv = st.session_state.get("invoice", {})

rows.append({"Item": "Kontrak - Nomor", "Value": k.get("nomor_kontrak")})
rows.append({"Item": "Kontrak - Tgl Mulai", "Value": (k.get("tanggal_mulai").isoformat() if isinstance(k.get("tanggal_mulai"), datetime) else k.get("tanggal_mulai_raw") )})
rows.append({"Item": "Kontrak - Tgl Selesai", "Value": (k.get("tanggal_selesai").isoformat() if isinstance(k.get("tanggal_selesai"), datetime) else k.get("tanggal_selesai_raw") )})
rows.append({"Item": "Kontrak - Nilai", "Value": k.get("nilai_kontrak")})
rows.append({"Item": "BA - Tanggal", "Value": (b.get("tanggal_ba").isoformat() if isinstance(b.get("tanggal_ba"), datetime) else b.get("tanggal_ba_raw"))})
rows.append({"Item": "BA - Status (vs Kontrak)", "Value": b.get("status")})
rows.append({"Item": "Invoice - Tanggal", "Value": (inv.get("tanggal_invoice").isoformat() if isinstance(inv.get("tanggal_invoice"), datetime) else inv.get("tanggal_invoice_raw"))})
rows.append({"Item": "Invoice - Date Status (vs BA)", "Value": inv.get("date_status")})
rows.append({"Item": "Invoice - Total", "Value": inv.get("total")})
rows.append({"Item": "Invoice - Amount Status (vs Kontrak)", "Value": inv.get("amount_status")})

df = pd.DataFrame(rows)
st.table(df)

# download CSV
csv = df.to_csv(index=False)
st.download_button("Download summary (CSV)", csv.encode("utf-8"), file_name="three_way_summary.csv")
