import streamlit as st
import pdfplumber
import pytesseract
from PIL import Image
import io
import re
import pandas as pd
import dateparser
import tempfile

# Fallback date pattern global
DATE_PATTERN_GLOBAL = r"(\d{1,2}\s*(Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s*\d{2,4})"

# Kontrak patterns (flexible)
CONTRACT_PATTERNS = {
    "nomor_kontrak": [r"Nomor\s*Kontrak\s*[:\-]?\s*(\S+)"],
    "tanggal_mulai": [r"Tanggal\s*Mulai\s*[:\-]?\s*([\w\s\d]+)"],
    "tanggal_selesai": [r"Tanggal\s*(Selesai|Berakhir)\s*[:\-]?\s*([\w\s\d]+)"],
    "nilai_kontrak": [r"(Rp[\s\d\.\,]+)"]
}

BA_PATTERNS = {
    "tanggal_ba": [r"Tanggal\s*[:\-]?\s*([\w\s\d]+)"]
}

INVOICE_PATTERNS = {
    "tanggal_invoice": [r"Tanggal\s*Invoice\s*[:\-]?\s*([\w\s\d]+)"],
    "nilai_invoice": [r"(Rp[\s\d\.\,]+)"]
}

# OCR fungsi
def ocr_pdf(file):
    text_all = ""
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            # Coba ambil teks langsung
            text = page.extract_text() or ""
            if len(text.strip()) < 10:
                # OCR jika teks terlalu sedikit
                img = page.to_image(resolution=300).original
                pil_img = Image.open(io.BytesIO(img))
                ocr_text = pytesseract.image_to_string(pil_img, lang="ind+eng")
                text_all += "\n" + ocr_text
            else:
                text_all += "\n" + text
    return text_all

# Aman regex search
def safe_search(pattern, text):
    try:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()
    except re.error:
        return None
    return None

# Fallback pencarian tanggal global
def extract_any_date(text):
    matches = re.findall(DATE_PATTERN_GLOBAL, text, flags=re.IGNORECASE)
    for match in matches:
        date_str = match[0]
        parsed = dateparser.parse(date_str, languages=["id", "en"])
        if parsed:
            return date_str, parsed
    return None, None

# Ekstraksi kontrak
def extract_contract(text):
    hasil = {}
    for key, patterns in CONTRACT_PATTERNS.items():
        val = None
        for p in patterns:
            val = safe_search(p, text)
            if val:
                break
        hasil[key] = val

    # Fallback tanggal
    if not hasil.get("tanggal_mulai") or not hasil.get("tanggal_selesai"):
        date_raw, date_parsed = extract_any_date(text)
        if date_raw:
            if not hasil.get("tanggal_mulai"):
                hasil["tanggal_mulai"] = date_raw
            elif not hasil.get("tanggal_selesai"):
                hasil["tanggal_selesai"] = date_raw
    return hasil

# Ekstraksi BA
def extract_ba(text):
    hasil = {}
    for key, patterns in BA_PATTERNS.items():
        val = None
        for p in patterns:
            val = safe_search(p, text)
            if val:
                break
        hasil[key] = val
    if not hasil.get("tanggal_ba"):
        date_raw, _ = extract_any_date(text)
        if date_raw:
            hasil["tanggal_ba"] = date_raw
    return hasil

# Ekstraksi invoice
def extract_invoice(text):
    hasil = {}
    for key, patterns in INVOICE_PATTERNS.items():
        val = None
        for p in patterns:
            val = safe_search(p, text)
            if val:
                break
        hasil[key] = val
    if not hasil.get("tanggal_invoice"):
        date_raw, _ = extract_any_date(text)
        if date_raw:
            hasil["tanggal_invoice"] = date_raw
    return hasil

# Streamlit UI
st.title("📄 Three-Way Matching OCR")

# Upload kontrak
st.header("1️⃣ Upload Kontrak")
kontrak_file = st.file_uploader("Pilih file kontrak", type=["pdf", "docx"])
if kontrak_file:
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(kontrak_file.read())
        kontrak_text = ocr_pdf(tmp.name)
    kontrak_data = extract_contract(kontrak_text)
    st.json(kontrak_data)

# Upload BA
st.header("2️⃣ Upload Berita Acara")
ba_file = st.file_uploader("Pilih file BA", type=["pdf", "docx"])
if ba_file:
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(ba_file.read())
        ba_text = ocr_pdf(tmp.name)
    ba_data = extract_ba(ba_text)
    st.json(ba_data)

# Upload Invoice
st.header("3️⃣ Upload Invoice")
invoice_file = st.file_uploader("Pilih file Invoice", type=["pdf", "docx"])
if invoice_file:
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(invoice_file.read())
        invoice_text = ocr_pdf(tmp.name)
    invoice_data = extract_invoice(invoice_text)
    st.json(invoice_data)
