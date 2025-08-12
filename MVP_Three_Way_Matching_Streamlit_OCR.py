# app.py
"""
Three-Way Matching — Fast-first + OCR fallback (full workflow)
- Prioritize page1 text extraction (fast). If page1 empty -> OCR page1 only (faster).
- If needed, OCR more pages (controlled by sidebar).
- Extract contract: nomor_kontrak (page1-priority), tanggal_mulai (page1), tanggal_selesai (computed from pasal duration),
  nilai_kontrak (from Pasal Biaya Pelaksanaan Pekerjaan -> fallback to Total/Nilai first Rp).
- Extract BA and Invoice, then do validations:
    - BA in contract period -> MATCH / NOT MATCH
    - Invoice date >= BA date -> MATCH / NOT MATCH
    - Invoice amount within tolerance vs contract -> MATCH / NOT MATCH
- Caches extraction per-file (hash) to avoid repeating OCR.
"""

import streamlit as st
import io, re, hashlib
from datetime import timedelta
import dateparser
import pandas as pd

# PDF & OCR libs
import pdfplumber
from pdf2image import convert_from_bytes
from PIL import Image
import pytesseract

# Page config
st.set_page_config(page_title="Three-Way Matching (Fast-OCR)", layout="wide")

# -------------------------
# Utilities
# -------------------------
def clean_text(txt: str) -> str:
    if not txt:
        return ""
    txt = txt.replace("\x00", " ")
    txt = re.sub(r"[\u0000-\u001F\u007F-\u009F]+", " ", txt)
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()

def safe_search(pattern, text, flags=re.IGNORECASE):
    if not pattern or not text:
        return None
    try:
        m = re.search(pattern, text, flags)
        return m.group(1).strip() if m else None
    except re.error:
        return None

def parse_date_flexible(s):
    if not s:
        return None
    return dateparser.parse(s, languages=["id","en"])

def normalize_amount_raw(s):
    if not s:
        return None
    s = str(s)
    s = s.replace("Rp", "").replace("rp", "").replace("IDR", "")
    s = s.replace(",-", "")
    s = re.sub(r"[^\d,\.]", "", s)
    if s.count(",") == 1 and s.count(".") > 1:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except Exception:
        return None

def file_sha256_bytes(b: bytes):
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()

# -------------------------
# Text extraction: page1-first, then OCR page1 only, then limited/full OCR
# -------------------------
def ocr_pil_image(pil_img, ocr_lang="ind", config="--psm 6"):
    try:
        return pytesseract.image_to_string(pil_img, lang=ocr_lang, config=config)
    except Exception:
        try:
            return pytesseract.image_to_string(pil_img)
        except Exception:
            return ""

def extract_text_page1_quick(bytes_data, ocr_lang="ind", dpi=200, psm=6):
    """
    Try to get textual page1 quickly:
    1) pdfplumber.extract_text() for page 1 (very fast)
    2) if empty -> OCR page1 only via pdfplumber page.to_image() or pdf2image if pdfplumber fails
    Returns (page1_text, full_text_estimate) where full_text_estimate may be empty.
    """
    tconfig = f"--psm {psm}"
    # try pdfplumber page 1
    try:
        with pdfplumber.open(io.BytesIO(bytes_data)) as pdf:
            if len(pdf.pages) == 0:
                return "", ""
            p = pdf.pages[0]
            page_text = p.extract_text() or ""
            if page_text.strip():
                return clean_text(page_text), ""
            # empty -> OCR page 1 using pdfplumber image (faster than convert_from_bytes)
            try:
                pil = p.to_image(resolution=dpi).original
                ocr_txt = ocr_pil_image(pil, ocr_lang=ocr_lang, config=tconfig)
                return clean_text(ocr_txt), ""
            except Exception:
                # fallback to pdf2image for page 1
                images = convert_from_bytes(bytes_data, dpi=dpi, first_page=1, last_page=1)
                if images:
                    ocr_txt = ocr_pil_image(images[0], ocr_lang=ocr_lang, config=tconfig)
                    return clean_text(ocr_txt), ""
                return "", ""
    except Exception:
        # pdfplumber failed; try pdf2image page1
        try:
            images = convert_from_bytes(bytes_data, dpi=dpi, first_page=1, last_page=1)
            if images:
                ocr_txt = ocr_pil_image(images[0], ocr_lang=ocr_lang, config=tconfig)
                return clean_text(ocr_txt), ""
        except Exception:
            pass
    return "", ""

def extract_text_limited_or_full(bytes_data, ocr_lang="ind", max_pages=3, dpi=200, psm=6):
    """
    Extract up to max_pages (if max_pages None: full document) using pdfplumber extract_text,
    performing OCR per-page when page text is short. If pdfplumber fails, convert_from_bytes + OCR pages.
    Returns full_text.
    """
    tconfig = f"--psm {psm}"
    full_text = ""
    try:
        with pdfplumber.open(io.BytesIO(bytes_data)) as pdf:
            pages = pdf.pages
            pages_to_iter = pages if (not max_pages or max_pages==0) else pages[:max_pages]
            for p in pages_to_iter:
                txt = p.extract_text() or ""
                if len(txt.strip()) < 20:
                    try:
                        pil = p.to_image(resolution=dpi).original
                        txt = ocr_pil_image(pil, ocr_lang=ocr_lang, config=tconfig) or txt
                    except Exception:
                        pass
                full_text += "\n" + (txt or "")
        return clean_text(full_text)
    except Exception:
        # fallback to convert_from_bytes + OCR pages
        try:
            pil_pages = convert_from_bytes(bytes_data, dpi=dpi)
            pages_to_iter = pil_pages if (not max_pages or max_pages==0) else pil_pages[:max_pages]
            for pil in pages_to_iter:
                txt = ocr_pil_image(pil, ocr_lang=ocr_lang, config=tconfig)
                full_text += "\n" + (txt or "")
            return clean_text(full_text)
        except Exception:
            return ""

# cache results per file + parameters
@st.cache_data(show_spinner=False)
def cached_full_extraction(file_hash: str, bytes_data: bytes, ocr_lang, max_pages, dpi, psm):
    page1_quick = extract_text_page1_quick(bytes_data, ocr_lang=ocr_lang, dpi=dpi, psm=psm)[0]
    full = extract_text_limited_or_full(bytes_data, ocr_lang=ocr_lang, max_pages=max_pages, dpi=dpi, psm=psm)
    return page1_quick, full

# -------------------------
# Domain extraction logic (flexible)
# -------------------------
DATE_PATTERN_LONG = r"(\d{1,2}\s+(?:Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s*\d{2,4})"
DURATION_PATTERN = r"(\d{1,4})\s*(?:hari|kalender|calendar)"
NOMOR_PATTERNS = [
    r"\b(SPERJ\.[A-Z0-9\./\-]+)\b",
    r"\b(Sperj\.[A-Z0-9\./\-]+)\b",
    r"Nomor\s*Kontrak\s*[:\-\s]*([A-Z0-9\/\.\-\_]+)",
    r"NOMOR\s*[:\-\s]*([A-Z0-9\/\.\-\_]+)",
    r"\b(No\.\s*[A-Z0-9\/\.\-\_]+)\b"
]
PASAL_BIAYA_HEADER = r"Pasal\s*\d+\s*.*?Biaya\s+Pelaksanaan\s+Pekerjaan"
RP_NUMBER_PATTERN = r"(Rp[\s]*[\d\.,\-\s]+(?:\d))"

def extract_contract_fields(full_text, page1_text):
    out = {
        "nomor_kontrak": None,
        "tanggal_mulai_raw": None,
        "tanggal_selesai_raw": None,
        "nilai_kontrak": None,
        "nilai_kontrak_raw": None,
        "duration_days": None
    }
    # nomor kontrak: page1 preferred then full
    for p in NOMOR_PATTERNS:
        v = safe_search(p, page1_text) or safe_search(p, full_text)
        if v:
            out["nomor_kontrak"] = v
            break
    # tanggal mulai: prefer page1
    v = safe_search(DATE_PATTERN_LONG, page1_text) or safe_search(DATE_PATTERN_LONG, full_text)
    if v:
        out["tanggal_mulai_raw"] = v
    # duration detection
    d = safe_search(DURATION_PATTERN, full_text)
    if d:
        try:
            out["duration_days"] = int(d)
        except Exception:
            out["duration_days"] = None
    # nilai kontrak: search Pasal Biaya block
    try:
        m = re.search(PASAL_BIAYA_HEADER, full_text, flags=re.IGNORECASE | re.DOTALL)
    except re.error:
        m = None
    if m:
        start = m.start()
        block = full_text[start:start+900]
        rp = safe_search(RP_NUMBER_PATTERN, block)
        if rp:
            out["nilai_kontrak_raw"] = rp
            out["nilai_kontrak"] = normalize_amount_raw(rp)
    # fallback: Total Biaya / Nilai Pekerjaan
    if not out["nilai_kontrak_raw"]:
        m2 = re.search(r"(Total\s+Biaya|Total\s*Nilai|Nilai\s+Pekerjaan).*?(Rp[\s\d\.,\-]+)", full_text, flags=re.IGNORECASE|re.DOTALL)
        if m2:
            rp = m2.group(2)
            out["nilai_kontrak_raw"] = rp
            out["nilai_kontrak"] = normalize_amount_raw(rp)
    # final fallback: first big Rp in doc
    if not out["nilai_kontrak_raw"]:
        anyrp = safe_search(RP_NUMBER_PATTERN, full_text)
        if anyrp:
            out["nilai_kontrak_raw"] = anyrp
            out["nilai_kontrak"] = normalize_amount_raw(anyrp)
    # compute tanggal_selesai if duration & start exist
    if out["duration_days"] and out["tanggal_mulai_raw"]:
        dt_start = parse_date_flexible(out["tanggal_mulai_raw"])
        if dt_start:
            dt_end = dt_start + timedelta(days=out["duration_days"])
            out["tanggal_selesai_raw"] = dt_end.strftime("%d %B %Y")
    return out

def extract_ba_fields(full_text, page1_text):
    out = {"tanggal_ba_raw": None, "tanggal_ba": None}
    v = safe_search(DATE_PATTERN_LONG, full_text) or safe_search(DATE_PATTERN_LONG, page1_text)
    if v:
        out["tanggal_ba_raw"] = v
        out["tanggal_ba"] = parse_date_flexible(v)
    return out

def extract_invoice_fields(full_text, page1_text):
    out = {"tanggal_invoice_raw": None, "tanggal_invoice": None, "dpp_raw": None, "ppn_raw": None, "total_raw": None, "total": None}
    v = safe_search(DATE_PATTERN_LONG, full_text) or safe_search(DATE_PATTERN_LONG, page1_text)
    if v:
        out["tanggal_invoice_raw"] = v
        out["tanggal_invoice"] = parse_date_flexible(v)
    dpp = safe_search(r"DPP[:\s\-]*(Rp[\s\d\.,\-]+)", full_text)
    ppn = safe_search(r"PPN[:\s\-]*(Rp[\s\d\.,\-]+)", full_text)
    total_match = safe_search(r"(Total\s*Invoice[:\s\-]*Rp[\s\d\.,\-]+)|(Jumlah[:\s\-]*Rp[\s\d\.,\-]+)", full_text)
    if total_match:
        rp = safe_search(RP_NUMBER_PATTERN, total_match)
        if rp:
            out["total_raw"] = rp
            out["total"] = normalize_amount_raw(rp)
    if dpp:
        out["dpp_raw"] = dpp
    if ppn:
        out["ppn_raw"] = ppn
    # fallback any Rp
    if not out["total_raw"]:
        anyrp = safe_search(RP_NUMBER_PATTERN, full_text)
        if anyrp:
            out["total_raw"] = anyrp
            out["total"] = normalize_amount_raw(anyrp)
    return out

# -------------------------
# UI (fast-first flow)
# -------------------------
st.title("Three-Way Matching — Fast + OCR (MVP)")

with st.sidebar:
    st.header("Speed & OCR settings")
    ocr_lang = st.selectbox("OCR language (single faster)", ["ind","ind+eng","eng"], index=0)
    max_pages = st.number_input("Max pages to OCR (0 = all)", min_value=0, max_value=200, value=2)
    dpi = st.select_slider("OCR DPI (lower = faster)", options=[150,200,250,300], value=200)
    force_ocr = st.checkbox("Force OCR for all pages (use only if doc is scanned)", value=False)
    psm = st.selectbox("Tesseract PSM (layout hint)", [6,3,4,11], index=0)
    tol_pct = st.number_input("Tolerance % for amount match", min_value=0.0, max_value=100.0, value=0.5)

col1, col2 = st.columns([1,2])

with col1:
    st.subheader("1) Upload Kontrak")
    kontrak_file = st.file_uploader("Kontrak (pdf/docx/image)", type=["pdf","docx","png","jpg","jpeg","tiff"])
    if st.button("Clear kontrak"):
        st.session_state.pop("kontrak", None)
with col2:
    st.subheader("Hasil Ekstraksi Kontrak")
    if "kontrak" in st.session_state:
        st.json(st.session_state["kontrak"])
    else:
        st.write("Belum ada kontrak")

def safe_rerun():
    try:
        st.rerun()
    except Exception:
        try:
            st.experimental_rerun()
        except Exception:
            pass

# handle kontrak upload with fast-first strategy
if kontrak_file:
    data_bytes = kontrak_file.read()
    fh = file_sha256_bytes(data_bytes)
    # quick page1 attempt (no OCR if page text exists)
    page1_quick, _ = cached_full_extraction(fh, data_bytes, ocr_lang, 1, dpi, psm)
    kontrak = extract_contract_fields("", page1_quick)  # use page1_quick as page1_text
    # if important fields missing -> run limited extraction controlled by max_pages (may OCR some pages)
    need_full = (not kontrak.get("nomor_kontrak") or not kontrak.get("tanggal_mulai_raw") or kontrak.get("nilai_kontrak") is None)
    if need_full:
        page1_full, full_text = cached_full_extraction(fh, data_bytes, ocr_lang, max_pages, dpi, psm)
        kontrak = extract_contract_fields(full_text, page1_full)
    st.session_state["kontrak"] = kontrak
    safe_rerun()

st.markdown("---")

with col1:
    st.subheader("2) Upload Berita Acara (BA)")
    ba_file = st.file_uploader("BA (pdf/docx/image)", type=["pdf","docx","png","jpg","jpeg","tiff"])
    if st.button("Clear BA"):
        st.session_state.pop("ba", None)
with col2:
    st.subheader("Hasil Ekstraksi BA")
    if "ba" in st.session_state:
        st.json(st.session_state["ba"])
    else:
        st.write("Belum ada BA")

if ba_file:
    data_bytes = ba_file.read()
    fh = file_sha256_bytes(data_bytes)
    page1, full_text = cached_full_extraction(fh, data_bytes, ocr_lang, max_pages, dpi, psm)
    ba = extract_ba_fields(full_text, page1)
    # validate BA vs contract
    if "kontrak" in st.session_state:
        k = st.session_state["kontrak"]
        try:
            start = parse_date_flexible(k.get("tanggal_mulai_raw")) if k.get("tanggal_mulai_raw") else None
            end = parse_date_flexible(k.get("tanggal_selesai_raw")) if k.get("tanggal_selesai_raw") else None
            tba = ba.get("tanggal_ba")
            if isinstance(tba, str):
                tba = parse_date_flexible(tba)
            if start and end and tba:
                ba["in_contract_period"] = (start.date() <= tba.date() <= end.date())
                ba["status"] = "MATCH" if ba["in_contract_period"] else "NOT MATCH"
        except Exception:
            pass
    st.session_state["ba"] = ba
    safe_rerun()

st.markdown("---")

with col1:
    st.subheader("3) Upload Invoice")
    invoice_file = st.file_uploader("Invoice (pdf/docx/image)", type=["pdf","docx","png","jpg","jpeg","tiff"])
    if st.button("Clear Invoice"):
        st.session_state.pop("invoice", None)
with col2:
    st.subheader("Hasil Ekstraksi Invoice")
    if "invoice" in st.session_state:
        st.json(st.session_state["invoice"])
    else:
        st.write("Belum ada invoice")

if invoice_file:
    data_bytes = invoice_file.read()
    fh = file_sha256_bytes(data_bytes)
    page1, full_text = cached_full_extraction(fh, data_bytes, ocr_lang, max_pages, dpi, psm)
    invoice = extract_invoice_fields(full_text, page1)
    # invoice date vs BA
    if "ba" in st.session_state and st.session_state["ba"].get("tanggal_ba") and invoice.get("tanggal_invoice"):
        try:
            tba = st.session_state["ba"].get("tanggal_ba")
            if isinstance(tba, str):
                tba = parse_date_flexible(tba)
            tinv = invoice.get("tanggal_invoice")
            if isinstance(tinv, str):
                tinv = parse_date_flexible(tinv)
            if tba and tinv:
                invoice["after_ba"] = (tinv.date() >= tba.date())
                invoice["date_status"] = "MATCH" if invoice["after_ba"] else "NOT MATCH"
        except Exception:
            pass
    # invoice amount vs contract
    if "kontrak" in st.session_state and st.session_state["kontrak"].get("nilai_kontrak") and invoice.get("total") is not None:
        try:
            contract_amt = st.session_state["kontrak"].get("nilai_kontrak")
            inv_amt = invoice.get("total")
            if contract_amt and inv_amt:
                tol = float(tol_pct) / 100.0
                invoice["amount_match"] = abs(inv_amt - contract_amt) <= (tol * contract_amt)
                invoice["amount_status"] = "MATCH" if invoice["amount_match"] else "NOT MATCH"
        except Exception:
            pass
    st.session_state["invoice"] = invoice
    safe_rerun()

st.markdown("---")

st.header("Ringkasan & Hasil Matching")
k = st.session_state.get("kontrak", {})
b = st.session_state.get("ba", {})
inv = st.session_state.get("invoice", {})

rows = [
    {"Item":"Kontrak - Nomor","Value":k.get("nomor_kontrak")},
    {"Item":"Kontrak - Tgl Mulai","Value":k.get("tanggal_mulai_raw")},
    {"Item":"Kontrak - Tgl Selesai","Value":k.get("tanggal_selesai_raw")},
    {"Item":"Kontrak - Nilai (raw)","Value":k.get("nilai_kontrak_raw")},
    {"Item":"Kontrak - Nilai (float)","Value":k.get("nilai_kontrak")},
    {"Item":"BA - Tanggal (raw)","Value":b.get("tanggal_ba_raw")},
    {"Item":"BA - Status (vs kontrak)","Value":b.get("status")},
    {"Item":"Invoice - Tanggal (raw)","Value":inv.get("tanggal_invoice_raw")},
    {"Item":"Invoice - Date Status (vs BA)","Value":inv.get("date_status")},
    {"Item":"Invoice - Total (raw)","Value":inv.get("total_raw")},
    {"Item":"Invoice - Total (float)","Value":inv.get("total")},
    {"Item":"Invoice - Amount Status (vs kontrak)","Value":inv.get("amount_status")},
]
df = pd.DataFrame(rows)
st.table(df)
st.download_button("Download summary (CSV)", df.to_csv(index=False).encode("utf-8"), file_name="three_way_summary.csv")
