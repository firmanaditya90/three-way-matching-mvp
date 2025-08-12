"""
Three-Way Matching (Streamlit) — Versi Aman + OCR
- Bisa ekstrak teks dari PDF, DOCX, dan hasil scan (gambar dalam PDF)
- Tangani error regex supaya tidak crash
- Bersih dari karakter aneh hasil OCR
"""

import streamlit as st
import pdfplumber
import docx
import re
import io
from datetime import datetime
import dateparser
import pandas as pd
from PIL import Image
import pytesseract
import easyocr

st.set_page_config(page_title="Three-Way Matching OCR", layout="wide")

# ----------- Utility Fungsi -----------

def clean_text(txt: str) -> str:
    """Bersihkan karakter aneh dari OCR supaya regex aman."""
    if not txt:
        return ""
    txt = txt.replace("\x00", " ")
    txt = re.sub(r"[\u0000-\u001F\u007F-\u009F]", " ", txt)  # remove control chars
    return txt.strip()

def ocr_image(img) -> str:
    """OCR gambar pakai pytesseract, fallback ke easyocr."""
    try:
        text = pytesseract.image_to_string(img, lang="ind+eng")
        if text.strip():
            return text
    except Exception:
        pass
    try:
        reader = easyocr.Reader(["id", "en"], gpu=False)
        result = reader.readtext(img)
        return " ".join([r[1] for r in result])
    except Exception:
        return ""

def extract_text_from_pdf(file) -> str:
    """Ekstrak teks PDF, fallback OCR jika kosong."""
    try:
        text = ""
        with pdfplumber.open(file) as pdf:
            for p in pdf.pages:
                page_text = p.extract_text() or ""
                if not page_text.strip():  # kalau kosong, coba OCR
                    img = p.to_image(resolution=300).original
                    page_text = ocr_image(img)
                text += page_text + "\n"
        return clean_text(text)
    except Exception:
        return ""

def extract_text_from_docx(file) -> str:
    """Ekstrak teks DOCX."""
    try:
        doc = docx.Document(file)
        full_text = [para.text for para in doc.paragraphs]
        return clean_text("\n".join(full_text))
    except Exception:
        return ""

def extract_text(uploaded_file):
    """Pilih metode ekstraksi sesuai format."""
    if uploaded_file is None:
        return ""
    name = uploaded_file.name.lower()
    if name.endswith(".pdf"):
        return extract_text_from_pdf(uploaded_file)
    if name.endswith(".docx") or name.endswith(".doc"):
        return extract_text_from_docx(uploaded_file)
    try:
        raw = uploaded_file.read()
        uploaded_file.seek(0)
        return clean_text(raw.decode("utf-8", errors="ignore"))
    except Exception:
        return ""

def safe_search(pattern, text, flags=re.IGNORECASE):
    """Cari regex tapi aman dari error pattern."""
    try:
        m = re.search(pattern, text, flags)
        return m.group(1).strip() if m else None
    except re.error:
        return None

def parse_date_flexible(text):
    if not text:
        return None
    return dateparser.parse(text, languages=["id", "en"])

def normalize_amount(s):
    if s is None:
        return None
    s = s.replace("Rp", "").replace("IDR", "")
    s = re.sub(r"[^0-9,\.]", "", s)
    if s.count(",") == 1 and s.count(".") > 1:
        s = s.replace(".", "")
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except Exception:
        return None

# ----------- Pola Ekstraksi -----------

CONTRACT_NUMBER_PATTERNS = [
    r"Nomor Kontrak[:\s]*([A-Za-z0-9\-/]+)",
    r"No\. Kontrak[:\s]*([A-Za-z0-9\-/]+)",
]
CONTRACT_START_PATTERNS = [
    r"Tanggal Mulai[:\s]*([\d\w ,.-]+)",
    r"Mulai[:\s]*([\d\w ,.-]+)",
]
CONTRACT_END_PATTERNS = [
    r"Tanggal Selesai[:\s]*([\d\w ,.-]+)",
    r"Selesai[:\s]*([\d\w ,.-]+)",
]
CONTRACT_VALUE_PATTERNS = [
    r"Nilai Pekerjaan[:\s]*Rp\s*([\d., ]+)",
    r"Nilai[:\s]*Rp\s*([\d., ]+)",
]
BA_DATE_PATTERNS = [
    r"Tanggal BA[:\s]*([\d\w ,.-]+)",
    r"Tanggal Berita Acara[:\s]*([\d\w ,.-]+)",
]
INVOICE_DATE_PATTERNS = [
    r"Tanggal Invoice[:\s]*([\d\w ,.-]+)",
    r"Tanggal Faktur[:\s]*([\d\w ,.-]+)",
]
INVOICE_TOTAL_PATTERNS = [
    r"Total[:\s]*Rp\s*([\d., ]+)",
    r"Jumlah[:\s]*Rp\s*([\d., ]+)",
]

# ----------- Ekstraksi -----------

def extract_contract(text):
    res = {}
    for p in CONTRACT_NUMBER_PATTERNS:
        val = safe_search(p, text)
        if val:
            res["nomor_kontrak"] = val
            break
    for p in CONTRACT_START_PATTERNS:
        val = safe_search(p, text)
        if val:
            res["tanggal_mulai_raw"] = val
            res["tanggal_mulai"] = parse_date_flexible(val)
            break
    for p in CONTRACT_END_PATTERNS:
        val = safe_search(p, text)
        if val:
            res["tanggal_selesai_raw"] = val
            res["tanggal_selesai"] = parse_date_flexible(val)
            break
    for p in CONTRACT_VALUE_PATTERNS:
        val = safe_search(p, text)
        if val:
            res["nilai_kontrak_raw"] = val
            res["nilai_kontrak"] = normalize_amount(val)
            break
    return res

def extract_ba(text):
    res = {}
    for p in BA_DATE_PATTERNS:
        val = safe_search(p, text)
        if val:
            res["tanggal_ba_raw"] = val
            res["tanggal_ba"] = parse_date_flexible(val)
            break
    return res

def extract_invoice(text):
    res = {}
    for p in INVOICE_DATE_PATTERNS:
        val = safe_search(p, text)
        if val:
            res["tanggal_invoice_raw"] = val
            res["tanggal_invoice"] = parse_date_flexible(val)
            break
    for p in INVOICE_TOTAL_PATTERNS:
        val = safe_search(p, text)
        if val:
            res["total_raw"] = val
            res["total"] = normalize_amount(val)
            break
    return res

# ----------- UI Streamlit -----------

st.title("Three-Way Matching — OCR & Regex Safe")
st.write("Upload Kontrak, BA, dan Invoice. Sistem otomatis baca teks + OCR untuk dokumen scan.")

# Kontrak
contract_file = st.file_uploader("Upload Kontrak", type=["pdf", "docx"])
if contract_file:
    kontrak = extract_contract(extract_text(contract_file))
    st.json(kontrak)

# BA
ba_file = st.file_uploader("Upload Berita Acara", type=["pdf", "docx"])
if ba_file:
    ba = extract_ba(extract_text(ba_file))
    st.json(ba)

# Invoice
invoice_file = st.file_uploader("Upload Invoice", type=["pdf", "docx"])
if invoice_file:
    invoice = extract_invoice(extract_text(invoice_file))
    st.json(invoice)
