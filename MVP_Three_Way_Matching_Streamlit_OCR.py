import streamlit as st
import pdfplumber
import pytesseract
from PIL import Image
import io, re
from datetime import timedelta
import dateparser

# --------------------
# OCR Helper
# --------------------
def ocr_pdf(file, lang="ind+eng"):
    text_all = ""
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            # Ambil teks asli
            text = page.extract_text() or ""
            if len(text.strip()) < 10:
                pil_img = page.to_image(resolution=300).original
                ocr_text = pytesseract.image_to_string(pil_img, lang=lang)
                text_all += "\n" + ocr_text
            else:
                text_all += "\n" + text
    return text_all

# --------------------
# Extract helpers
# --------------------
def find_first_regex(patterns, text):
    if isinstance(patterns, str):
        patterns = [patterns]
    for p in patterns:
        m = re.search(p, text, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None

def extract_duration_days(text):
    m = re.search(r"(\d+)\s*(hari|calendar|kalender)", text, flags=re.IGNORECASE)
    return int(m.group(1)) if m else None

def calculate_end_date(start_date_str, days):
    if not start_date_str or not days:
        return None
    parsed_start = dateparser.parse(start_date_str, languages=["id", "en"])
    if parsed_start:
        return parsed_start + timedelta(days=days)
    return None

# --------------------
# Contract extraction
# --------------------
def extract_contract(text):
    hasil = {}

    # Nomor kontrak (halaman awal)
    hasil["nomor_kontrak"] = find_first_regex([
        r"Nomor\s*Kontrak\s*[:\-]?\s*([A-Z0-9\/\.\-\_]+)",
        r"NOMOR\s*[:\-]?\s*([A-Z0-9\/\.\-\_]+)"
    ], text)

    # Tanggal kontrak (tanggal mulai pekerjaan)
    hasil["tanggal_mulai"] = find_first_regex(
        r"(\d{1,2}\s+(Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember)\s+\d{4})",
        text
    )

    # Cari durasi hari dari pasal jangka waktu
    duration_days = extract_duration_days(text)
    if duration_days and hasil.get("tanggal_mulai"):
        tanggal_selesai = calculate_end_date(hasil["tanggal_mulai"], duration_days)
        if tanggal_selesai:
            hasil["tanggal_selesai"] = tanggal_selesai.strftime("%d %B %Y")
    else:
        hasil["tanggal_selesai"] = None

    # Nilai kontrak dari pasal biaya pelaksanaan pekerjaan
    biaya_block_match = re.search(
        r"(Pasal\s+\d+\s+.*?Biaya\s+Pelaksanaan\s+Pekerjaan.*?)(Rp[\d\.\,]+)",
        text, re.IGNORECASE | re.DOTALL
    )
    if biaya_block_match:
        hasil["nilai_kontrak"] = biaya_block_match.group(2)
    else:
        # fallback global
        nilai = find_first_regex(r"(Rp\s*[\d\.\,]+)", text)
        hasil["nilai_kontrak"] = nilai

    return hasil

# --------------------
# BA extraction
# --------------------
def extract_ba(text):
    hasil = {}
    hasil["tanggal_ba"] = find_first_regex(
        r"(\d{1,2}\s+(Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember)\s+\d{4})",
        text
    )
    return hasil

# --------------------
# Invoice extraction
# --------------------
def extract_invoice(text):
    hasil = {}
    hasil["tanggal_invoice"] = find_first_regex(
        r"(\d{1,2}\s+(Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember)\s+\d{4})",
        text
    )
    hasil["nilai_invoice"] = find_first_regex(r"(Rp\s*[\d\.\,]+)", text)
    return hasil

# --------------------
# Streamlit UI
# --------------------
st.title("Three Way Matching - OCR Version")

# Upload Kontrak
st.header("Upload Kontrak")
kontrak_file = st.file_uploader("Upload file kontrak", type=["pdf", "docx"])
if kontrak_file:
    kontrak_text = ocr_pdf(kontrak_file)
    kontrak_data = extract_contract(kontrak_text)
    st.json(kontrak_data)

# Upload Berita Acara
st.header("Upload Berita Acara")
ba_file = st.file_uploader("Upload file BA", type=["pdf", "docx"])
if ba_file:
    ba_text = ocr_pdf(ba_file)
    ba_data = extract_ba(ba_text)
    st.json(ba_data)

# Upload Invoice
st.header("Upload Invoice")
invoice_file = st.file_uploader("Upload file Invoice", type=["pdf", "docx"])
if invoice_file:
    invoice_text = ocr_pdf(invoice_file)
    invoice_data = extract_invoice(invoice_text)
    st.json(invoice_data)

# Matching logic
if kontrak_file and ba_file and invoice_file:
    st.header("Hasil Matching")
    match_ba = "Match"
    match_invoice = "Match"

    # BA vs Kontrak
    if kontrak_data.get("tanggal_selesai") and ba_data.get("tanggal_ba"):
        ba_date = dateparser.parse(ba_data["tanggal_ba"], languages=["id", "en"])
        kontrak_end = dateparser.parse(kontrak_data["tanggal_selesai"], languages=["id", "en"])
        if ba_date > kontrak_end:
            match_ba = "Not Match"

    # Invoice vs BA
    if invoice_data.get("tanggal_invoice") and ba_data.get("tanggal_ba"):
        inv_date = dateparser.parse(invoice_data["tanggal_invoice"], languages=["id", "en"])
        ba_date = dateparser.parse(ba_data["tanggal_ba"], languages=["id", "en"])
        if inv_date < ba_date:
            match_invoice = "Not Match"

    # Nilai invoice vs kontrak
    if invoice_data.get("nilai_invoice") and kontrak_data.get("nilai_kontrak"):
        if invoice_data["nilai_invoice"] != kontrak_data["nilai_kontrak"]:
            match_invoice = "Not Match"

    st.write("BA vs Kontrak:", match_ba)
    st.write("Invoice vs BA & Nilai:", match_invoice)
