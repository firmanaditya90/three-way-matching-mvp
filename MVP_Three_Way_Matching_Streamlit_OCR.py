import streamlit as st
import pdfplumber
import pytesseract
from pdf2image import convert_from_bytes
from PIL import Image
import io
import re
import dateparser
from datetime import datetime, timedelta

st.set_page_config(page_title="Three Way Matching", layout="wide")

# --- Fungsi ekstraksi PDF ---
def extract_text_from_pdf(pdf_file):
    """Ekstrak teks dari PDF, jika kosong fallback ke OCR."""
    text = ""
    try:
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                text += page_text + "\n"
    except:
        text = ""

    if not text.strip():
        # OCR fallback
        pdf_file.seek(0)
        images = convert_from_bytes(pdf_file.read())
        for img in images:
            text += pytesseract.image_to_string(img, lang="ind") + "\n"
    return text

# --- Fungsi ekstraksi kontrak ---
def parse_contract(text):
    result = {
        "nomor_kontrak": None,
        "tanggal_mulai": None,
        "tanggal_selesai": None,
        "nilai_kontrak": None,
        "duration_days": None
    }

    # Ambil halaman 1 saja untuk pencarian cepat
    page1 = text.split("\n\n")[0] if text else ""

    # Nomor kontrak
    nomor_match = re.search(r"\bSperj\.\s*\d+\/[A-Z]+\.\d+\/[A-Z]+-\d{4}\b", page1, re.IGNORECASE)
    if nomor_match:
        result["nomor_kontrak"] = nomor_match.group(0).strip()

    # Tanggal kontrak (mulai)
    tanggal_match = re.search(r"\b\d{1,2}\s+(Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember|Jan|Feb|Mar|Apr|Mei|Jun|Jul|Agu|Sep|Okt|Nov|Des)\s+\d{4}\b", page1, re.IGNORECASE)
    if tanggal_match:
        tanggal_obj = dateparser.parse(tanggal_match.group(0), languages=["id", "en"])
        if tanggal_obj:
            result["tanggal_mulai"] = tanggal_obj.strftime("%d %B %Y")

    # Durasi hari dari pasal jangka waktu
    durasi_match = re.search(r"(\d+)\s+hari\s+kalender", text, re.IGNORECASE)
    if durasi_match and result["tanggal_mulai"]:
        try:
            durasi = int(durasi_match.group(1))
            result["duration_days"] = durasi
            mulai_obj = dateparser.parse(result["tanggal_mulai"], languages=["id", "en"])
            if mulai_obj:
                selesai_obj = mulai_obj + timedelta(days=durasi)
                result["tanggal_selesai"] = selesai_obj.strftime("%d %B %Y")
        except:
            pass

    # Nilai kontrak dari pasal biaya
    nilai_match = re.search(r"Rp\s?[\d\.\,]+", text, re.IGNORECASE)
    if nilai_match:
        result["nilai_kontrak"] = nilai_match.group(0).replace(" ,", ",").strip()

    return result

# --- Upload dan proses ---
st.title("📄 Three Way Matching - MVP")

uploaded_kontrak = st.file_uploader("Upload Kontrak", type=["pdf"])
if uploaded_kontrak:
    text_kontrak = extract_text_from_pdf(uploaded_kontrak)
    data_kontrak = parse_contract(text_kontrak)
    st.subheader("📋 Data Kontrak")
    st.json(data_kontrak)

# Placeholder untuk BA & Invoice bisa ditambahkan di bawah
