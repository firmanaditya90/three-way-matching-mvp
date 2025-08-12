import streamlit as st
import pdfplumber
import pytesseract
from pdf2image import convert_from_bytes
import io
import re
import dateparser
from datetime import timedelta

# ============== HELPER FUNCTIONS =================

def read_pdf_fast(file_bytes, ocr_lang="ind"):
    """Coba baca PDF cepat dengan pdfplumber, jika kosong baru OCR halaman pertama."""
    text = ""
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages[:1]:  # hanya halaman 1 dulu
                txt = page.extract_text() or ""
                text += txt
    except Exception:
        text = ""
    
    if not text.strip():  # jika kosong, pakai OCR
        images = convert_from_bytes(file_bytes, first_page=1, last_page=1)
        text = pytesseract.image_to_string(images[0], lang=ocr_lang)
    
    return text

def extract_nomor_kontrak(text):
    match = re.search(r"(?:Nomor|No)\s*[:\-]?\s*([A-Za-z0-9\/\.\-]+)", text, re.IGNORECASE)
    return match.group(1) if match else None

def extract_tanggal_kontrak(text):
    match = re.search(r"(\d{1,2}\s+(?:Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember)\s+\d{4})", text, re.IGNORECASE)
    return match.group(1) if match else None

def extract_durasi(text):
    match = re.search(r"(\d{1,3})\s*hari\s*kalender", text, re.IGNORECASE)
    return int(match.group(1)) if match else None

def extract_nilai_kontrak(text):
    match = re.search(r"Rp\s*([\d\.\,]+)", text)
    return match.group(1) if match else None

def extract_tanggal_ba(text):
    match = re.search(r"(\d{1,2}\s+(?:Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember)\s+\d{4})", text, re.IGNORECASE)
    return match.group(1) if match else None

def extract_invoice_data(text):
    tgl = extract_tanggal_ba(text)
    dpp = re.search(r"DPP\s*Rp\s*([\d\.\,]+)", text)
    ppn = re.search(r"PPN\s*Rp\s*([\d\.\,]+)", text)
    total = re.search(r"Rp\s*([\d\.\,]+)", text)
    return {
        "tanggal": tgl,
        "dpp": dpp.group(1) if dpp else None,
        "ppn": ppn.group(1) if ppn else None,
        "total": total.group(1) if total else None
    }

# ============== STREAMLIT APP =================

st.title("📑 Three Way Matching - Hutang Usaha")

# Step 1: Upload Kontrak
st.header("1️⃣ Upload Kontrak")
kontrak_file = st.file_uploader("Upload file kontrak (PDF)", type=["pdf"])
kontrak_data = {}

if kontrak_file:
    kontrak_text = read_pdf_fast(kontrak_file.read())
    kontrak_data["nomor_kontrak"] = extract_nomor_kontrak(kontrak_text)
    kontrak_data["tanggal_mulai_raw"] = extract_tanggal_kontrak(kontrak_text)
    kontrak_data["duration_days"] = extract_durasi(kontrak_text)
    kontrak_data["nilai_kontrak"] = extract_nilai_kontrak(kontrak_text)

    # Hitung tanggal selesai jika ada durasi
    if kontrak_data["tanggal_mulai_raw"] and kontrak_data["duration_days"]:
        mulai = dateparser.parse(kontrak_data["tanggal_mulai_raw"])
        selesai = mulai + timedelta(days=kontrak_data["duration_days"])
        kontrak_data["tanggal_selesai_raw"] = selesai.strftime("%d %B %Y")
    else:
        kontrak_data["tanggal_selesai_raw"] = None

    st.json(kontrak_data)

# Step 2: Upload BA
st.header("2️⃣ Upload Berita Acara (BA)")
ba_file = st.file_uploader("Upload file BA (PDF)", type=["pdf"])
ba_tanggal = None
if ba_file:
    ba_text = read_pdf_fast(ba_file.read())
    ba_tanggal_raw = extract_tanggal_ba(ba_text)
    ba_tanggal = dateparser.parse(ba_tanggal_raw) if ba_tanggal_raw else None
    st.write(f"Tanggal BA: {ba_tanggal_raw or 'Tidak ditemukan'}")

# Step 3: Upload Invoice
st.header("3️⃣ Upload Invoice")
inv_file = st.file_uploader("Upload file Invoice (PDF)", type=["pdf"])
invoice_data = {}
if inv_file:
    inv_text = read_pdf_fast(inv_file.read())
    invoice_data = extract_invoice_data(inv_text)
    invoice_data["tanggal_parsed"] = dateparser.parse(invoice_data["tanggal"]) if invoice_data["tanggal"] else None
    st.json(invoice_data)

# Step 4: Three Way Matching
st.header("📊 Hasil Matching")
if kontrak_data and ba_tanggal and invoice_data:
    hasil = {}

    # Match BA vs Kontrak (tanggal)
    k_mulai = dateparser.parse(kontrak_data.get("tanggal_mulai_raw")) if kontrak_data.get("tanggal_mulai_raw") else None
    k_selesai = dateparser.parse(kontrak_data.get("tanggal_selesai_raw")) if kontrak_data.get("tanggal_selesai_raw") else None
    if k_mulai and k_selesai:
        hasil["BA_vs_Kontrak"] = "MATCH" if k_mulai <= ba_tanggal <= k_selesai else "NOT MATCH"
    else:
        hasil["BA_vs_Kontrak"] = "DATA TIDAK LENGKAP"

    # Match Invoice vs BA (tanggal)
    inv_tgl = invoice_data.get("tanggal_parsed")
    if inv_tgl and ba_tanggal:
        hasil["Invoice_vs_BA"] = "MATCH" if inv_tgl >= ba_tanggal else "NOT MATCH"
    else:
        hasil["Invoice_vs_BA"] = "DATA TIDAK LENGKAP"

    # Match Nilai Invoice vs Kontrak
    if kontrak_data.get("nilai_kontrak") and invoice_data.get("total"):
        hasil["Nilai_Invoice_vs_Kontrak"] = "MATCH" if kontrak_data["nilai_kontrak"] == invoice_data["total"] else "NOT MATCH"
    else:
        hasil["Nilai_Invoice_vs_Kontrak"] = "DATA TIDAK LENGKAP"

    st.json(hasil)
