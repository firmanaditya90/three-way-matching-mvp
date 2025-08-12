# app.py
"""
Three-Way Matching — Robust OCR + Page1-priority + Pasal Biaya Pelaksanaan
- Upload Kontrak (PDF/DOCX/image), BA, Invoice
- Extract nomor kontrak (halaman 1), tanggal mulai (halaman 1),
  tanggal selesai otomatis dari pasal jangka waktu (jika ada),
  nilai kontrak dari Pasal Biaya Pelaksanaan Pekerjaan (prioritas).
- Fallback OCR untuk scanned PDFs.
- Aman dari re errors.
"""

import streamlit as st
import io
import re
from datetime import timedelta, datetime
import dateparser
import pandas as pd

# PDF handling & OCR
import pdfplumber
from pdf2image import convert_from_bytes
from PIL import Image
import pytesseract

st.set_page_config(page_title="Three-Way Matching (Full, OCR Safe)", layout="wide")

# -------------------------
# Utility helpers
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
    d = dateparser.parse(s, languages=["id","en"])
    return d

def normalize_amount_raw(s):
    """Return float or None. Accepts strings like 'Rp 8.430.203.580,-'."""
    if not s:
        return None
    s = str(s)
    s = s.replace("Rp", "").replace("rp", "").replace("IDR", "")
    s = s.replace(",-", "")
    s = re.sub(r"[^\d,\.]", "", s)
    # handle thousand separators: assume dot thousands, comma decimals (Indonesia)
    if s.count(",") == 1 and s.count(".") > 1:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except Exception:
        return None

# -------------------------
# Text extraction with OCR fallback
# -------------------------
def extract_text_and_page1(bytes_data, ocr_lang="ind+eng", max_pages=None):
    """
    Try pdfplumber extract_text for pages. If pdfplumber fails or page text too small,
    fallback convert pages -> images (pdf2image) and OCR each page.
    Returns: full_text (str), page1_text (str)
    """
    full_text = ""
    page1_text = ""
    # Try reading with pdfplumber first
    try:
        with pdfplumber.open(io.BytesIO(bytes_data)) as pdf:
            pages = pdf.pages
            if len(pages) == 0:
                raise Exception("No pages")
            total = len(pages)
            pages_to_iter = pages if not max_pages else pages[:max_pages]
            for i, p in enumerate(pages_to_iter):
                txt = p.extract_text() or ""
                # If extracted text is too short, run OCR on this page
                if len(txt.strip()) < 20:
                    try:
                        pil_img = p.to_image(resolution=300).original  # PIL Image
                        ocr = pytesseract.image_to_string(pil_img, lang=ocr_lang)
                        txt = ocr or txt
                    except Exception:
                        # fallback leave txt as-is (maybe empty)
                        pass
                full_text += "\n" + txt
                if i == 0:
                    page1_text = txt
    except Exception:
        # fallback full OCR via pdf2image
        try:
            pil_pages = convert_from_bytes(bytes_data, dpi=300)
            for i, pil in enumerate(pil_pages):
                try:
                    ocr = pytesseract.image_to_string(pil, lang=ocr_lang)
                except Exception:
                    ocr = ""
                full_text += "\n" + (ocr or "")
                if i == 0:
                    page1_text = (ocr or "")
                if max_pages and i+1 >= max_pages:
                    break
        except Exception:
            # ultimate fallback: return empty strings
            return "", ""
    return clean_text(full_text), clean_text(page1_text)

def extract_text_from_uploaded(uploaded_file, ocr_lang="ind+eng", max_pages=None):
    if uploaded_file is None:
        return "", ""
    # If docx or image, handle separately
    name = uploaded_file.name.lower()
    data = uploaded_file.read()
    uploaded_file.seek(0)
    if name.endswith(".pdf"):
        return extract_text_and_page1(data, ocr_lang=ocr_lang, max_pages=max_pages)
    # DOCX: try to extract text with python-docx (if installed). fallback: OCR if it's an image inside pdf, but we assume docx textual.
    if name.endswith(".docx") or name.endswith(".doc"):
        try:
            import docx
            doc = docx.Document(io.BytesIO(data))
            paragraphs = [p.text for p in doc.paragraphs]
            txt = "\n".join(paragraphs)
            return clean_text(txt), clean_text("\n".join(paragraphs[:30]))
        except Exception:
            # fallback: OCR attempt by opening as image (rare)
            pass
    # image
    if name.endswith((".png",".jpg",".jpeg",".tiff",".bmp")):
        try:
            pil = Image.open(io.BytesIO(data))
            ocr = pytesseract.image_to_string(pil, lang=ocr_lang)
            return clean_text(ocr), clean_text(ocr)
        except Exception:
            return "", ""
    return "", ""

# -------------------------
# Domain-specific extraction
# -------------------------
# Patterns
DATE_PATTERN_LONG = r"(\d{1,2}\s+(?:Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s*\d{2,4})"
DURATION_PATTERN = r"(\d{1,4})\s*(?:hari|kalender|calendar)"
# Nomor kontrak patterns (more permissive)
NOMOR_PATTERNS = [
    r"Nomor\s*Kontrak\s*[:\-\s]*([A-Z0-9\/\.\-\_]+)",
    r"NOMOR\s*[:\-\s]*([A-Z0-9\/\.\-\_]+)",
    r"Nomor\s*[:\-\s]*([A-Z0-9\/\.\-\_]+)"
]
# Pasal Biaya Pelaksanaan: search block header then nearby Rp
PASAL_BIAYA_HEADER = r"Pasal\s*\d+\s*.*?Biaya\s+Pelaksanaan\s+Pekerjaan"
RP_NUMBER_PATTERN = r"(Rp[\s]*[\d\.,\-\s]+(?:\d))"  # match Rp followed by digits/.,-

def extract_contract_fields(full_text, page1_text):
    """
    Return dict:
      nomor_kontrak, tanggal_mulai (string), tanggal_selesai (string or None),
      nilai_kontrak_raw (string), nilai_kontrak (float or None)
    """
    res = {
        "nomor_kontrak": None,
        "tanggal_mulai_raw": None,
        "tanggal_selesai_raw": None,
        "nilai_kontrak_raw": None,
        "nilai_kontrak": None,
        "duration_days": None
    }

    # 1) nomor kontrak: search page1 first, then full_text
    for p in NOMOR_PATTERNS:
        v = safe_search(p, page1_text) or safe_search(p, full_text)
        if v:
            res["nomor_kontrak"] = v
            break

    # 2) tanggal mulai: try page1 (first 30 lines) then full
    # page1_text may be large; try long date pattern
    v = safe_search(DATE_PATTERN_LONG, page1_text)
    if not v:
        v = safe_search(DATE_PATTERN_LONG, full_text)
    if v:
        res["tanggal_mulai_raw"] = v

    # 3) duration days detection (from pasal jangka waktu or anywhere)
    dmatch = safe_search(DURATION_PATTERN, full_text)
    if dmatch:
        try:
            res["duration_days"] = int(dmatch)
        except Exception:
            res["duration_days"] = None

    # 4) nilai kontrak: search Pasal Biaya Pelaksanaan area
    # find header location
    try:
        m = re.search(PASAL_BIAYA_HEADER, full_text, flags=re.IGNORECASE|re.DOTALL)
    except re.error:
        m = None
    if m:
        start = m.start()
        # take block after header (e.g., next 300 chars)
        block = full_text[start:start+800]
        rp = safe_search(RP_NUMBER_PATTERN, block)
        if rp:
            res["nilai_kontrak_raw"] = rp
            res["nilai_kontrak"] = normalize_amount_raw(rp)
    # fallback: search for "Total Biaya" or "Total Nilai" near Rp
    if not res["nilai_kontrak_raw"]:
        m2 = re.search(r"(Total\s+Biaya|Total\s*Nilai|Nilai\s+Pekerjaan).*?(Rp[\s\d\.,\-]+)", full_text, flags=re.IGNORECASE|re.DOTALL)
        if m2:
            rp = m2.group(2)
            res["nilai_kontrak_raw"] = rp
            res["nilai_kontrak"] = normalize_amount_raw(rp)
    # global fallback: first big Rp in doc
    if not res["nilai_kontrak_raw"]:
        rp_global = safe_search(RP_NUMBER_PATTERN, full_text)
        if rp_global:
            res["nilai_kontrak_raw"] = rp_global
            res["nilai_kontrak"] = normalize_amount_raw(rp_global)

    # 5) compute tanggal_selesai if duration and start date found
    if res["duration_days"] and res["tanggal_mulai_raw"]:
        dt_start = parse_date_flexible(res["tanggal_mulai_raw"])
        if dt_start:
            dt_end = dt_start + timedelta(days=res["duration_days"])
            res["tanggal_selesai_raw"] = dt_end.strftime("%d %B %Y")

    return res

def extract_ba_fields(full_text, page1_text):
    res = {"tanggal_ba_raw": None, "tanggal_ba": None}
    v = safe_search(DATE_PATTERN_LONG, full_text) or safe_search(DATE_PATTERN_LONG, page1_text)
    if v:
        res["tanggal_ba_raw"] = v
        res["tanggal_ba"] = parse_date_flexible(v)
    return res

def extract_invoice_fields(full_text, page1_text):
    res = {"tanggal_invoice_raw": None, "tanggal_invoice": None, "total_raw": None, "total": None}
    # date
    v = safe_search(DATE_PATTERN_LONG, full_text) or safe_search(DATE_PATTERN_LONG, page1_text)
    if v:
        res["tanggal_invoice_raw"] = v
        res["tanggal_invoice"] = parse_date_flexible(v)
    # DPP / PPN / Total patterns
    dpp = safe_search(r"DPP[:\s\-]*(Rp[\s\d\.,\-]+)", full_text)
    ppn = safe_search(r"PPN[:\s\-]*(Rp[\s\d\.,\-]+)", full_text)
    total = safe_search(r"(Total\s*Invoice[:\s\-]*Rp[\s\d\.,\-]+)|(Jumlah[:\s\-]*Rp[\s\d\.,\-]+)", full_text)
    # total could be a concatenation; normalize
    if total:
        # extract Rp part
        rp = safe_search(RP_NUMBER_PATTERN, total)
        if rp:
            res["total_raw"] = rp
            res["total"] = normalize_amount_raw(rp)
    # fallback search any Rp
    if not res["total_raw"]:
        rp_any = safe_search(RP_NUMBER_PATTERN, full_text)
        if rp_any:
            res["total_raw"] = rp_any
            res["total"] = normalize_amount_raw(rp_any)
    return res

# -------------------------
# UI & main flow
# -------------------------
st.title("Three-Way Matching — Full Version (OCR-safe, Pasal-aware)")

with st.sidebar:
    st.header("Settings")
    ocr_lang = st.selectbox("OCR language", ["ind+eng","ind","eng"], index=0)
    max_pages = st.number_input("Max pages to OCR (0 = all)", min_value=0, max_value=200, value=0)
    tol_pct = st.number_input("Tolerance % for amount match", min_value=0.0, max_value=100.0, value=0.5)

col1, col2 = st.columns([1,2])

with col1:
    st.subheader("1) Upload Kontrak (PDF/DOCX/image)")
    kontrak_file = st.file_uploader("Kontrak", type=["pdf","docx","png","jpg","jpeg","tiff"])
    if st.button("Clear kontrak"):
        st.session_state.pop("kontrak", None)
with col2:
    st.subheader("Ekstraksi Kontrak")
    if "kontrak" in st.session_state:
        st.json(st.session_state["kontrak"])
    else:
        st.write("Belum ada kontrak")

if kontrak_file:
    bytes_data = kontrak_file.read()
    # use max_pages param
    mp = None if max_pages==0 else max_pages
    full_text, page1_text = extract_text_and_page1(bytes_data, ocr_lang=ocr_lang, max_pages=mp)
    kontrak = extract_contract_fields(full_text, page1_text)
    st.session_state["kontrak"] = kontrak
    st.experimental_rerun()

st.markdown("---")

col3, col4 = st.columns([1,2])
with col3:
    st.subheader("2) Upload Berita Acara (BA)")
    ba_file = st.file_uploader("BA", type=["pdf","docx","png","jpg","jpeg","tiff"])
    if st.button("Clear BA"):
        st.session_state.pop("ba", None)
with col4:
    st.subheader("Ekstraksi BA")
    if "ba" in st.session_state:
        st.json(st.session_state["ba"])
    else:
        st.write("Belum ada BA")

if ba_file:
    bytes_data = ba_file.read()
    mp = None if max_pages==0 else max_pages
    full_text, page1_text = extract_text_and_page1(bytes_data, ocr_lang=ocr_lang, max_pages=mp)
    ba = extract_ba_fields(full_text, page1_text)
    # validate BA vs contract period if we have kontrak
    if "kontrak" in st.session_state:
        k = st.session_state["kontrak"]
        try:
            if k.get("tanggal_mulai_raw"):
                start = parse_date_flexible(k.get("tanggal_mulai_raw"))
            else:
                start = None
            if k.get("tanggal_selesai_raw"):
                end = parse_date_flexible(k.get("tanggal_selesai_raw"))
            else:
                end = None
            if ba.get("tanggal_ba"):
                tba = ba.get("tanggal_ba")
            else:
                tba = None
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
    invoice_file = st.file_uploader("Invoice", type=["pdf","docx","png","jpg","jpeg","tiff"])
    if st.button("Clear Invoice"):
        st.session_state.pop("invoice", None)
with col6:
    st.subheader("Ekstraksi Invoice")
    if "invoice" in st.session_state:
        st.json(st.session_state["invoice"])
    else:
        st.write("Belum ada invoice")

if invoice_file:
    bytes_data = invoice_file.read()
    mp = None if max_pages==0 else max_pages
    full_text, page1_text = extract_text_and_page1(bytes_data, ocr_lang=ocr_lang, max_pages=mp)
    invoice = extract_invoice_fields(full_text, page1_text)
    # verify invoice date vs BA
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
    # verify amount vs contract
    if "kontrak" in st.session_state and st.session_state["kontrak"].get("nilai_kontrak") and invoice.get("total") is not None:
        try:
            contract_amt = st.session_state["kontrak"].get("nilai_kontrak")
            inv_amt = invoice.get("total")
            if contract_amt and inv_amt:
                tol = float(tol_pct)/100.0
                invoice["amount_match"] = abs(inv_amt - contract_amt) <= (tol * contract_amt)
                invoice["amount_status"] = "MATCH" if invoice["amount_match"] else "NOT MATCH"
        except Exception:
            pass
    st.session_state["invoice"] = invoice
    st.experimental_rerun()

st.markdown("---")

st.header("Ringkasan & Hasil Matching")
k = st.session_state.get("kontrak", {})
b = st.session_state.get("ba", {})
inv = st.session_state.get("invoice", {})

rows = [
    {"Item":"Kontrak - Nomor","Value":k.get("nomor_kontrak")},
    {"Item":"Kontrak - Tanggal Mulai","Value":k.get("tanggal_mulai_raw")},
    {"Item":"Kontrak - Tanggal Selesai","Value":k.get("tanggal_selesai_raw")},
    {"Item":"Kontrak - Nilai (raw)","Value":k.get("nilai_kontrak_raw")},
    {"Item":"Kontrak - Nilai (float)","Value":k.get("nilai_kontrak")},
    {"Item":"BA - Tanggal (raw)","Value":b.get("tanggal_ba_raw")},
    {"Item":"BA - Status (vs kontrak)","Value":b.get("status")},
    {"Item":"Invoice - Tanggal (raw)","Value":inv.get("tanggal_invoice_raw")},
    {"Item":"Invoice - Date Status (vs BA)","Value":inv.get("date_status")},
    {"Item":"Invoice - Total (raw)","Value":inv.get("total_raw")},
    {"Item":"Invoice - Total (float)","Value":inv.get("total")},
    {"Item":"Invoice - Amount Status (vs kontrak)","Value":inv.get("amount_status")}
]
df = pd.DataFrame(rows)
st.table(df)

csv = df.to_csv(index=False)
st.download_button("Download CSV", csv.encode("utf-8"), file_name="three_way_summary.csv")
