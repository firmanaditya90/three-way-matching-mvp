# app.py
"""
Three-Way Matching — Full, OCR-safe, Page1-priority, Pasal-aware
- Upload Kontrak (PDF/DOCX/image), BA, Invoice
- Extract nomor kontrak (halaman 1), tanggal mulai (halaman 1),
  tanggal selesai auto from pasal jangka waktu, nilai kontrak from Pasal Biaya Pelaksanaan Pekerjaan
- Fallback OCR for scanned PDFs (pdf2image + pytesseract)
- Safe regex (no PatternError), caching per-file, performance options
"""

import streamlit as st
import io, re, hashlib
from datetime import timedelta
import dateparser
import pandas as pd

# PDF/OCR libs
import pdfplumber
from pdf2image import convert_from_bytes
from PIL import Image
import pytesseract

# ---------- Config ----------
st.set_page_config(page_title="Three-Way Matching (MVP)", layout="wide")

# ---------- Utilities ----------
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
    # Indonesian style: dot thousands, comma decimals
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

# ---------- Text extraction with pdfplumber + pdf2image fallback ----------
def extract_text_and_page1_bytes(bytes_data, ocr_lang="ind+eng", max_pages=None, dpi=300, force_ocr=False, psm=6):
    """
    Return tuple (full_text, page1_text).
    - tries pdfplumber, OCR pages with pytesseract when text short or force_ocr True.
    - if pdfplumber fails => convert_from_bytes + OCR all pages.
    """
    full_text = ""
    page1_text = ""
    tconfig = f"--psm {psm}"
    # try pdfplumber
    if not force_ocr:
        try:
            with pdfplumber.open(io.BytesIO(bytes_data)) as pdf:
                pages = pdf.pages
                if len(pages) == 0:
                    raise RuntimeError("No pages in PDF")
                pages_to_iter = pages if not max_pages else pages[:max_pages]
                for i, p in enumerate(pages_to_iter):
                    txt = p.extract_text() or ""
                    if len(txt.strip()) < 20 or force_ocr:
                        try:
                            pil = p.to_image(resolution=dpi).original  # PIL Image
                            ocr_txt = pytesseract.image_to_string(pil, lang=ocr_lang, config=tconfig)
                            txt = ocr_txt or txt
                        except Exception:
                            pass
                    full_text += "\n" + (txt or "")
                    if i == 0:
                        page1_text = txt or ""
            return clean_text(full_text), clean_text(page1_text)
        except Exception:
            # fallback to pdf2image+OCR
            pass

    # full OCR fallback
    try:
        pil_pages = convert_from_bytes(bytes_data, dpi=dpi)
        for i, pil in enumerate(pil_pages):
            try:
                ocr_txt = pytesseract.image_to_string(pil, lang=ocr_lang, config=tconfig)
            except Exception:
                ocr_txt = ""
            full_text += "\n" + (ocr_txt or "")
            if i == 0:
                page1_text = ocr_txt or ""
            if max_pages and i+1 >= max_pages:
                break
        return clean_text(full_text), clean_text(page1_text)
    except Exception:
        return "", ""

# ---------- Cache wrapper to avoid reprocessing same file ----------
@st.cache_data(show_spinner=False)
def extract_text_cached(file_hash: str, bytes_data: bytes, ocr_lang, max_pages, dpi, force_ocr, psm):
    # cache key includes file_hash and params
    return extract_text_and_page1_bytes(bytes_data, ocr_lang=ocr_lang, max_pages=max_pages, dpi=dpi, force_ocr=force_ocr, psm=psm)

def extract_text_from_uploaded(upl, ocr_lang="ind+eng", max_pages=None, dpi=300, force_ocr=False, psm=6):
    if upl is None:
        return "", ""
    name = upl.name.lower()
    data = upl.read()
    upl.seek(0)
    if name.endswith(".pdf"):
        fh = file_sha256_bytes(data)
        full, p1 = extract_text_cached(fh, data, ocr_lang, max_pages, dpi, force_ocr, psm)
        return full, p1
    if name.endswith((".docx", ".doc")):
        try:
            import docx
            doc = docx.Document(io.BytesIO(data))
            paras = [p.text for p in doc.paragraphs]
            txt = "\n".join(paras)
            page1 = "\n".join(paras[:30])
            return clean_text(txt), clean_text(page1)
        except Exception:
            return "", ""
    if name.endswith((".png",".jpg",".jpeg",".tiff",".bmp")):
        try:
            pil = Image.open(io.BytesIO(data))
            tconfig = f"--psm {psm}"
            ocr_txt = pytesseract.image_to_string(pil, lang=ocr_lang)
            return clean_text(ocr_txt), clean_text(ocr_txt)
        except Exception:
            return "", ""
    return "", ""

# ---------- Domain-specific extraction ----------
DATE_PATTERN_LONG = r"(\d{1,2}\s+(?:Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s*\d{2,4})"
DURATION_PATTERN = r"(\d{1,4})\s*(?:hari|kalender|calendar)"
NOMOR_PATTERNS = [
    r"Nomor\s*Kontrak\s*[:\-\s]*([A-Z0-9\/\.\-\_]+)",
    r"NOMOR\s*[:\-\s]*([A-Z0-9\/\.\-\_]+)",
    r"Nomor\s*[:\-\s]*([A-Z0-9\/\.\-\_]+)"
]
PASAL_BIAYA_HEADER = r"Pasal\s*\d+\s*.*?Biaya\s+Pelaksanaan\s+Pekerjaan"
RP_NUMBER_PATTERN = r"(Rp[\s]*[\d\.,\-\s]+(?:\d))"

def extract_contract_fields(full_text, page1_text):
    out = {
        "nomor_kontrak": None,
        "tanggal_mulai_raw": None,
        "tanggal_selesai_raw": None,
        "nilai_kontrak_raw": None,
        "nilai_kontrak": None,
        "duration_days": None
    }
    # Nomor kontrak: try page1 then full
    for p in NOMOR_PATTERNS:
        v = safe_search(p, page1_text) or safe_search(p, full_text)
        if v:
            out["nomor_kontrak"] = v
            break
    # tanggal mulai: page1 preferred
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
    # nilai kontrak: look for pasal biaya block
    try:
        m = re.search(PASAL_BIAYA_HEADER, full_text, flags=re.IGNORECASE | re.DOTALL)
    except re.error:
        m = None
    if m:
        start = m.start()
        block = full_text[start:start+800]
        rp = safe_search(RP_NUMBER_PATTERN, block)
        if rp:
            out["nilai_kontrak_raw"] = rp
            out["nilai_kontrak"] = normalize_amount_raw(rp)
    # fallback: Total Biaya / Nilai Pekerjaan
    if not out["nilai_kontrak_raw"]:
        m2 = re.search(r"(Total\s+Biaya|Total\s*Nilai|Nilai\s+Pekerjaan).*?(Rp[\s\d\.,\-]+)", full_text, flags=re.IGNORECASE | re.DOTALL)
        if m2:
            rp = m2.group(2)
            out["nilai_kontrak_raw"] = rp
            out["nilai_kontrak"] = normalize_amount_raw(rp)
    # final fallback: any large Rp
    if not out["nilai_kontrak_raw"]:
        anyrp = safe_search(RP_NUMBER_PATTERN, full_text)
        if anyrp:
            out["nilai_kontrak_raw"] = anyrp
            out["nilai_kontrak"] = normalize_amount_raw(anyrp)
    # compute tanggal selesai if duration & start present
    if out["duration_days"] and out["tanggal_mulai_raw"]:
        ds = parse_date_flexible(out["tanggal_mulai_raw"])
        if ds:
            de = ds + timedelta(days=out["duration_days"])
            out["tanggal_selesai_raw"] = de.strftime("%d %B %Y")
    return out

def extract_ba_fields(full_text, page1_text):
    out = {"tanggal_ba_raw": None, "tanggal_ba": None}
    v = safe_search(DATE_PATTERN_LONG, full_text) or safe_search(DATE_PATTERN_LONG, page1_text)
    if v:
        out["tanggal_ba_raw"] = v
        out["tanggal_ba"] = parse_date_flexible(v)
    return out

def extract_invoice_fields(full_text, page1_text):
    out = {"tanggal_invoice_raw": None, "tanggal_invoice": None, "total_raw": None, "total": None}
    v = safe_search(DATE_PATTERN_LONG, full_text) or safe_search(DATE_PATTERN_LONG, page1_text)
    if v:
        out["tanggal_invoice_raw"] = v
        out["tanggal_invoice"] = parse_date_flexible(v)
    total_match = safe_search(r"(Total\s*Invoice[:\s\-]*Rp[\s\d\.,\-]+)|(Jumlah[:\s\-]*Rp[\s\d\.,\-]+)", full_text)
    if total_match:
        rp = safe_search(RP_NUMBER_PATTERN, total_match)
        if rp:
            out["total_raw"] = rp
            out["total"] = normalize_amount_raw(rp)
    if not out["total_raw"]:
        rp_any = safe_search(RP_NUMBER_PATTERN, full_text)
        if rp_any:
            out["total_raw"] = rp_any
            out["total"] = normalize_amount_raw(rp_any)
    return out

# ---------- UI ----------
st.title("Three-Way Matching — Full (MVP)")

with st.sidebar:
    st.header("Performance & OCR settings")
    ocr_lang = st.selectbox("OCR language (choose single if you want speed)", ["ind+eng","ind","eng"], index=0)
    max_pages = st.number_input("Max pages to OCR (0 = all)", min_value=0, max_value=200, value=3)
    dpi = st.select_slider("OCR DPI (lower = faster)", options=[150,200,250,300], value=250)
    force_ocr = st.checkbox("Force OCR for all pages (use if PDF is scanned)", value=False)
    psm = st.selectbox("Tesseract PSM (layout hint)", [6,3,4,11], index=0)  # 6 = assume a single uniform block of text
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

if kontrak_file:
    bytes_data = kontrak_file.read()
    mp = None if max_pages==0 else max_pages
    full_text, page1 = extract_text_and_page1_bytes(bytes_data, ocr_lang=ocr_lang, max_pages=mp, dpi=dpi, force_ocr=force_ocr, psm=psm)
    kontrak = extract_contract_fields(full_text, page1)
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
    bytes_data = ba_file.read()
    mp = None if max_pages==0 else max_pages
    full_text, page1 = extract_text_and_page1_bytes(bytes_data, ocr_lang=ocr_lang, max_pages=mp, dpi=dpi, force_ocr=force_ocr, psm=psm)
    ba = extract_ba_fields(full_text, page1)
    # validate BA within contract
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
    bytes_data = invoice_file.read()
    mp = None if max_pages==0 else max_pages
    full_text, page1 = extract_text_and_page1_bytes(bytes_data, ocr_lang=ocr_lang, max_pages=mp, dpi=dpi, force_ocr=force_ocr, psm=psm)
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
    # invoice amount vs kontrak
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
    {"Item":"Invoice - Amount Status (vs kontrak)","Value":inv.get("amount_status")},
]
df = pd.DataFrame(rows)
st.table(df)
st.download_button("Download summary (CSV)", df.to_csv(index=False).encode("utf-8"), file_name="three_way_summary.csv")
