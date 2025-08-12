import streamlit as st
import pdfplumber
import pytesseract
from PIL import Image
import io
import re
import dateparser
import tempfile

# Pattern global untuk cari tanggal fallback
DATE_PATTERN_GLOBAL = r"(\d{1,2}\s*(Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s*\d{2,4})"

# Kontrak patterns
CONTRACT_PATTERNS = {
    "nomor_kontrak": [r"Nomor\s*Kontrak\s*[:\-]?\s*(\S+)"],
    "tanggal_mulai": [r"Tanggal\s*Mulai\s*[:\-]?\s*([\w\s\d]+)"],
    "tanggal_selesai": [r"Tanggal\s*(Selesai|Berakhir)\s*[:\-]?\s*([\w\s\d]+)"],
    "nilai_kontrak": [r"(Rp[\s\d\.\,]+)"]
}

# BA patterns
BA_PATTERNS = {
    "tanggal_ba": [r"Tanggal\s*[:\-]?\s*([\w\s\d]+)"]
}

# Invoice patterns
INVOICE_PATTERNS = {
    "tanggal_invoice": [r"Tanggal\s*Invoice\s*[:\-]?\s*([\w\s\d]+)"],
    "nilai_invoice": [r"(Rp[\s\d\.\,]+)"]
}

# Fungsi OCR dengan fallback full image
def ocr_pdf(file_path, lang="ind+eng"):
    text_all = ""
    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                if len(text.strip()) < 10:
                    pil_img = page.to_image(resolution=300).original
                    ocr_text = pytesseract.image_to_string(pil_img, lang=lang)
                    text_all += "\n" + ocr_text
                else:
                    text_all += "\n" + text
    except Exception:
        # Fallback: convert semua halaman ke image lalu OCR
        from pdf2image import convert_from_path
        pages = convert_from_path(file_path, dpi=300)
        for pil_img in pages:
            ocr_text = pytesseract.image_to_string(pil_img, lang=lang)
            text_all += "\n" + ocr_text
    return text_all

# Regex aman
def safe_search(pattern, text):
    try:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()
    except re.error:
        return None
    return None

# Fallback tanggal global
def extract_any_date(text):
    matches = re.findall(DATE_PATTERN_GLOBAL, text, flags=re.IGNORECASE)
    for match in matches:
        date_str = match[0]
        parsed = dateparser.parse(date_str, languages=["id", "en"])
        if parsed:
            return date_str, parsed
    return None, None

# Ekstraksi data
def extract_data(text, patterns):
    hasil = {}
    for key, pats in patterns.items():
        val = None
        for p in pats:
            val = safe_search(p, text)
            if val:
                break
        hasil[key] = val
    return hasil

# Ekstraksi kontrak dengan fallback tanggal
def extract_contract(text):
    hasil = extract_data(text, CONTRACT_PATTERNS)
    if not hasil.get("tanggal_mulai") or not hasil.get("tanggal_selesai"):
        date_raw, _ = extract_any_date(text)
        if date_raw:
            if not hasil.get("tanggal_mulai"):
                hasil["tanggal_mulai"] = date_raw
            elif not hasil.get("tanggal_selesai"):
                hasil["tanggal_selesai"] = date_raw
    return hasil

# Ekstraksi BA
def extract_ba(text):
    hasil = extract_data(text, BA_PATTERNS)
    if not hasil.get("tanggal_ba"):
        date_raw, _ = extract_any_date(text)
        if date_raw:
            hasil["tanggal_ba"] = date_raw
    return hasil

# Ekstraksi invoice
def extract_invoice(text):
    hasil = extract_data(text, INVOICE_PATTERNS)
    if not hasil.get("tanggal_invoice"):
        date_raw, _ = extract_any_date(text)
        if date_raw:
            hasil["tanggal_invoice"] = date_raw
    return hasil

# UI
st.title("📄 Three-Way Matching OCR - Versi Aman")

# Upload kontrak
st.header("1️⃣ Upload Kontrak")
kontrak_file = st.file_uploader("Pilih file kontrak (PDF)", type=["pdf"])
if kontrak_file:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(kontrak_file.read())
        kontrak_text = ocr_pdf(tmp.name)
    kontrak_data = extract_contract(kontrak_text)
    st.subheader("📋 Data Kontrak")
    st.json(kontrak_data)

# Upload BA
st.header("2️⃣ Upload Berita Acara")
ba_file = st.file_uploader("Pilih file BA (PDF)", type=["pdf"])
if ba_file:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(ba_file.read())
        ba_text = ocr_pdf(tmp.name)
    ba_data = extract_ba(ba_text)
    st.subheader("📋 Data BA")
    st.json(ba_data)

# Upload Invoice
st.header("3️⃣ Upload Invoice")
invoice_file = st.file_uploader("Pilih file Invoice (PDF)", type=["pdf"])
if invoice_file:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(invoice_file.read())
        invoice_text = ocr_pdf(tmp.name)
    invoice_data = extract_invoice(invoice_text)
    st.subheader("📋 Data Invoice")
    st.json(invoice_data)
