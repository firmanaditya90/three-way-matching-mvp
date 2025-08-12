import streamlit as st
import pdfplumber
import pytesseract
from pdf2image import convert_from_bytes
import io, re, json
from datetime import datetime, timedelta
import dateparser

# Konfigurasi OCR
pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"

# Fungsi baca PDF cepat
def read_pdf_text(file_bytes, max_pages=1):
    text = ""
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for i, page in enumerate(pdf.pages):
                if max_pages and i >= max_pages:
                    break
                text += page.extract_text() or ""
    except Exception:
        pass
    return text

# OCR fallback
def ocr_pdf_first_page(file_bytes):
    images = convert_from_bytes(file_bytes, first_page=1, last_page=1)
    text = pytesseract.image_to_string(images[0], lang="ind")
    return text

# Ekstraksi kontrak
def extract_kontrak(text):
    # Nomor kontrak
    nomor = re.search(r"(SPERJ\.?|PKS|No\.?|Nomor)\s*[:\-]?\s*([A-Z0-9\/\.\-]+)", text, re.IGNORECASE)
    nomor_kontrak = nomor.group(2) if nomor else None

    # Tanggal mulai
    tgl_mulai_match = re.search(r"(\d{1,2}\s+(Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember|Jan|Feb|Mar|Apr|Mei|Jun|Jul|Agu|Sep|Okt|Nov|Des)\s+\d{4})", text)
    tanggal_mulai = dateparser.parse(tgl_mulai_match.group(1)) if tgl_mulai_match else None

    # Durasi
    durasi_match = re.search(r"(\d{1,3})\s+hari", text, re.IGNORECASE)
    durasi = int(durasi_match.group(1)) if durasi_match else None

    # Tanggal selesai
    tanggal_selesai = (tanggal_mulai + timedelta(days=durasi)) if (tanggal_mulai and durasi) else None

    # Nilai kontrak
    nilai_match = re.search(r"Rp[\s\.0-9]+", text)
    nilai_kontrak = nilai_match.group(0) if nilai_match else None

    return {
        "nomor_kontrak": nomor_kontrak,
        "tanggal_mulai": tanggal_mulai.strftime("%d-%m-%Y") if tanggal_mulai else None,
        "tanggal_selesai": tanggal_selesai.strftime("%d-%m-%Y") if tanggal_selesai else None,
        "duration_days": durasi,
        "nilai_kontrak": nilai_kontrak
    }

# Ekstraksi tanggal dari BA & Invoice
def extract_tanggal(text):
    tgl_match = re.search(r"(\d{1,2}\s+(Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember|Jan|Feb|Mar|Apr|Mei|Jun|Jul|Agu|Sep|Okt|Nov|Des)\s+\d{4})", text)
    return dateparser.parse(tgl_match.group(1)) if tgl_match else None

# Nilai Invoice
def extract_invoice_values(text):
    dpp_match = re.search(r"DPP.*?Rp[\s\.0-9]+", text, re.IGNORECASE)
    ppn_match = re.search(r"PPN.*?Rp[\s\.0-9]+", text, re.IGNORECASE)
    total_match = re.search(r"Total.*?Rp[\s\.0-9]+", text, re.IGNORECASE)
    return {
        "dpp": dpp_match.group(0) if dpp_match else None,
        "ppn": ppn_match.group(0) if ppn_match else None,
        "total": total_match.group(0) if total_match else None
    }

# Streamlit UI
st.title("📑 Three Way Matching - Hutang Usaha")

# Upload kontrak
kontrak_file = st.file_uploader("Upload Kontrak", type=["pdf"])
ba_file = st.file_uploader("Upload Berita Acara (BA)", type=["pdf"])
invoice_file = st.file_uploader("Upload Invoice", type=["pdf"])

result = {}

if kontrak_file:
    kontrak_bytes = kontrak_file.read()
    text = read_pdf_text(kontrak_bytes, max_pages=2)
    if not text.strip():
        text = ocr_pdf_first_page(kontrak_bytes)
    result["kontrak"] = extract_kontrak(text)

if ba_file:
    ba_bytes = ba_file.read()
    text = read_pdf_text(ba_bytes, max_pages=1)
    if not text.strip():
        text = ocr_pdf_first_page(ba_bytes)
    result["ba_tanggal"] = extract_tanggal(text)

if invoice_file:
    inv_bytes = invoice_file.read()
    text = read_pdf_text(inv_bytes, max_pages=1)
    if not text.strip():
        text = ocr_pdf_first_page(inv_bytes)
    result["invoice_tanggal"] = extract_tanggal(text)
    result["invoice_values"] = extract_invoice_values(text)

# Three-way matching check
if "kontrak" in result and "ba_tanggal" in result:
    k_mulai = dateparser.parse(result["kontrak"]["tanggal_mulai"])
    k_selesai = dateparser.parse(result["kontrak"]["tanggal_selesai"])
    ba_tgl = result["ba_tanggal"]
    result["ba_vs_kontrak"] = "MATCH" if (ba_tgl and k_mulai <= ba_tgl <= k_selesai) else "NOT MATCH"

if "ba_tanggal" in result and "invoice_tanggal" in result:
    ba_tgl = result["ba_tanggal"]
    inv_tgl = result["invoice_tanggal"]
    result["invoice_vs_ba"] = "MATCH" if (inv_tgl and inv_tgl >= ba_tgl) else "NOT MATCH"

if "kontrak" in result and "invoice_values" in result:
    kontrak_val = result["kontrak"]["nilai_kontrak"]
    inv_total = result["invoice_values"]["total"]
    result["invoice_vs_kontrak"] = "MATCH" if (kontrak_val and inv_total and kontrak_val in inv_total) else "NOT MATCH"

# Output JSON
if result:
    st.subheader("Hasil Ekstraksi & Matching")
    st.json(result)
    st.download_button("Download Hasil JSON", data=json.dumps(result, ensure_ascii=False), file_name="hasil_matching.json", mime="application/json")
