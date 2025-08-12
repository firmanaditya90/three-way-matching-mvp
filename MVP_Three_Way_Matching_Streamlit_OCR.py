# app.py
"""
Three-Way Matching (Fast-first + OCR fallback)
- Prioritize page1(no-OCR) for speed; fallback OCR on page1 only if needed, then limited/full OCR.
- Extract contract fields (nomor_kontrak, tanggal_mulai, tanggal_selesai, nilai_kontrak),
  extract BA (tanggal), extract Invoice (tanggal, dpp, ppn, total),
  perform Three-Way Matching (BA vs Contract, Invoice vs BA, Invoice vs Contract amount).
- Cache per-file (hash) to avoid reprocessing same file.
"""

import streamlit as st
import io, re, hashlib, json
from datetime import timedelta
import dateparser
import pandas as pd

# OCR & PDF libs
import pdfplumber
from pdf2image import convert_from_bytes
from PIL import Image
import pytesseract

# Page config
st.set_page_config(page_title="Three-Way Matching (Fast+OCR)", layout="wide")

# ---------------------------
# Utilities
# ---------------------------
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
    # handle Indonesian style thousand separators
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

# ---------------------------
# OCR helpers
# ---------------------------
def ocr_pil_image(pil_img, ocr_lang="ind", config="--psm 6"):
    try:
        return pytesseract.image_to_string(pil_img, lang=ocr_lang, config=config)
    except Exception:
        try:
            return pytesseract.image_to_string(pil_img)
        except Exception:
            return ""

def extract_page1_quick(bytes_data, ocr_lang="ind", dpi=200, psm=6):
    """
    Try to obtain page1 text quickly:
    1) pdfplumber page.extract_text() for page1 (fast)
    2) if empty -> OCR page1 only via pdfplumber page.to_image() or convert_from_bytes fallback
    Returns page1_text
    """
    tconfig = f"--psm {psm}"
    try:
        with pdfplumber.open(io.BytesIO(bytes_data)) as pdf:
            if not pdf.pages:
                return ""
            p = pdf.pages[0]
            txt = p.extract_text() or ""
            if txt and txt.strip():
                return clean_text(txt)
            # page has no text (likely scanned) -> OCR page1 via pdfplumber to_image if available
            try:
                pil = p.to_image(resolution=dpi).original
                return clean_text(ocr_pil_image(pil, ocr_lang=ocr_lang, config=tconfig))
            except Exception:
                # fallback to pdf2image for page 1
                imgs = convert_from_bytes(bytes_data, dpi=dpi, first_page=1, last_page=1)
                if imgs:
                    return clean_text(ocr_pil_image(imgs[0], ocr_lang=ocr_lang, config=tconfig))
                return ""
    except Exception:
        # pdfplumber failed (maybe not a pdf) -> try pdf2image page1
        try:
            imgs = convert_from_bytes(bytes_data, dpi=dpi, first_page=1, last_page=1)
            if imgs:
                return clean_text(ocr_pil_image(imgs[0], ocr_lang=ocr_lang, config=tconfig))
        except Exception:
            pass
    return ""

def extract_limited_or_full(bytes_data, ocr_lang="ind", max_pages=3, dpi=200, psm=6):
    """
    Extract up to max_pages or full text if max_pages==0.
    Uses pdfplumber.extract_text per page and OCR when page extract_text is short.
    """
    tconfig = f"--psm {psm}"
    full_text = ""
    try:
        with pdfplumber.open(io.BytesIO(bytes_data)) as pdf:
            pages = pdf.pages
            pages_to_iter = pages if (not max_pages or max_pages == 0) else pages[:max_pages]
            for p in pages_to_iter:
                txt = p.extract_text() or ""
                if len(txt.strip()) < 20:
                    try:
                        pil = p.to_image(resolution=dpi).original
                        ocr_txt = ocr_pil_image(pil, ocr_lang=ocr_lang, config=tconfig)
                        txt = ocr_txt or txt
                    except Exception:
                        pass
                full_text += "\n" + (txt or "")
        return clean_text(full_text)
    except Exception:
        # fallback to convert_from_bytes + OCR pages
        try:
            imgs = convert_from_bytes(bytes_data, dpi=dpi)
            imgs_to_iter = imgs if (not max_pages or max_pages == 0) else imgs[:max_pages]
            for pil in imgs_to_iter:
                txt = ocr_pil_image(pil, ocr_lang=ocr_lang, config=tconfig)
                full_text += "\n" + (txt or "")
            return clean_text(full_text)
        except Exception:
            return ""

# Cache extraction by file hash and parameters
@st.cache_data(show_spinner=False)
def cached_extraction(file_hash: str, bytes_data: bytes, ocr_lang, max_pages, dpi, psm):
    page1 = extract_page1_quick(bytes_data, ocr_lang=ocr_lang, dpi=dpi, psm=psm)
    full = extract_limited_or_full(bytes_data, ocr_lang=ocr_lang, max_pages=max_pages, dpi=dpi, psm=psm)
    return page1, full

# ---------------------------
# Extraction rules (domain)
# ---------------------------
DATE_PATTERN_LONG = r"(\d{1,2}\s+(?:Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s*\d{2,4})"
DURATION_PATTERN = r"(\d{1,4})\s*(?:\(|\s)?(?:\([^\)]*\))?\s*(?:hari|kalender|hari kalender|hari kerja|hari kerja)"
NOMOR_PATTERNS = [
    r"\b(SPERJ\.[A-Z0-9\./\-]+)\b",
    r"\b(Sperj\.[A-Z0-9\./\-]+)\b",
    r"Nomor\s*Kontrak\s*[:\-\s]*([A-Z0-9\/\.\-\_]+)",
    r"NOMOR\s*[:\-\s]*([A-Z0-9\/\.\-\_]+)",
    r"\b(No\.\s*[A-Z0-9\/\.\-\_]+)\b",
    r"\b([A-Z]{2,6}\.?[0-9A-Z\./\-]{6,})\b"   # fallback: long uppercase/digit sequence
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
    # nomor kontrak: page1 preferred then full_text
    for p in NOMOR_PATTERNS:
        v = safe_search(p, page1_text) or safe_search(p, full_text)
        if v:
            out["nomor_kontrak"] = v.strip().strip(":,.;")
            break
    # tanggal mulai: page1 preferred then full_text; also look for signature phrase
    sig = safe_search(r"ditandatangani\s+pada\s+tanggal[:\s]*([\d\w\s\.,\-]+)", full_text)
    if sig:
        # try to extract date fragment from sig
        d = safe_search(DATE_PATTERN_LONG, sig)
        if d:
            out["tanggal_mulai_raw"] = d
    if not out["tanggal_mulai_raw"]:
        d = safe_search(DATE_PATTERN_LONG, page1_text) or safe_search(DATE_PATTERN_LONG, full_text)
        if d:
            out["tanggal_mulai_raw"] = d
    # duration detection (pasal jangka waktu)
    dmatch = safe_search(DURATION_PATTERN, full_text)
    if dmatch:
        try:
            out["duration_days"] = int(dmatch)
        except:
            out["duration_days"] = None
    # nilai kontrak: search Pasal Biaya first (near header)
    try:
        m = re.search(PASAL_BIAYA_HEADER, full_text, flags=re.IGNORECASE|re.DOTALL)
    except re.error:
        m = None
    if m:
        start = m.start()
        block = full_text[start:start+1200]
        rp = safe_search(RP_NUMBER_PATTERN, block)
        if rp:
            rp_clean = rp.strip().strip(".,;")
            out["nilai_kontrak_raw"] = rp_clean
            out["nilai_kontrak"] = normalize_amount_raw(rp_clean)
    # fallback: Total Biaya / Nilai Pekerjaan
    if not out["nilai_kontrak_raw"]:
        m2 = re.search(r"(Total\s+Biaya|Total\s*Nilai|Nilai\s+Pekerjaan).*?(Rp[\s\d\.,\-]+)", full_text, flags=re.IGNORECASE|re.DOTALL)
        if m2:
            rp = m2.group(2)
            rp_clean = rp.strip().strip(".,;")
            out["nilai_kontrak_raw"] = rp_clean
            out["nilai_kontrak"] = normalize_amount_raw(rp_clean)
    # final fallback: first large Rp in doc
    if not out["nilai_kontrak_raw"]:
        anyrp = safe_search(RP_NUMBER_PATTERN, full_text)
        if anyrp:
            anyrp_clean = anyrp.strip().strip(".,;")
            out["nilai_kontrak_raw"] = anyrp_clean
            out["nilai_kontrak"] = normalize_amount_raw(anyrp_clean)
    # compute tanggal_selesai if duration & tanggal_mulai exist
    if out["duration_days"] and out["tanggal_mulai_raw"]:
        dt_start = parse_date_flexible(out["tanggal_mulai_raw"])
        if dt_start:
            dt_end = dt_start + timedelta(days=out["duration_days"])
            out["tanggal_selesai_raw"] = dt_end.strftime("%d %B %Y")
    return out

def extract_ba_fields(full_text, page1_text):
    out = {"tanggal_ba_raw": None, "tanggal_ba": None}
    d = safe_search(DATE_PATTERN_LONG, full_text) or safe_search(DATE_PATTERN_LONG, page1_text)
    if d:
        out["tanggal_ba_raw"] = d
        out["tanggal_ba"] = parse_date_flexible(d)
    return out

def extract_invoice_fields(full_text, page1_text):
    out = {"tanggal_invoice_raw": None, "tanggal_invoice": None, "dpp_raw": None, "ppn_raw": None, "total_raw": None, "total": None}
    d = safe_search(DATE_PATTERN_LONG, full_text) or safe_search(DATE_PATTERN_LONG, page1_text)
    if d:
        out["tanggal_invoice_raw"] = d
        out["tanggal_invoice"] = parse_date_flexible(d)
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
    if not out["total_raw"]:
        anyrp = safe_search(RP_NUMBER_PATTERN, full_text)
        if anyrp:
            out["total_raw"] = anyrp
            out["total"] = normalize_amount_raw(anyrp)
    return out

# ---------------------------
# UI & Flow
# ---------------------------
st.title("Three-Way Matching — Fast + OCR (MVP)")

with st.sidebar:
    st.header("Performance & OCR")
    ocr_lang = st.selectbox("OCR language", ["ind","ind+eng","eng"], index=0)
    max_pages = st.number_input("Max pages to OCR (0 = all)", min_value=0, max_value=200, value=2)
    dpi = st.select_slider("OCR DPI (lower = faster)", options=[150,200,250,300], value=200)
    force_ocr = st.checkbox("Force OCR for all pages (if file is scanned)", value=False)
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

# Process kontrak with fast-first strategy
if kontrak_file:
    data_bytes = kontrak_file.read()
    fh = file_sha256_bytes(data_bytes)
    # quick page1 (no-OCR if page has text; otherwise OCR page1)
    page1_quick, full_quick = cached_extraction(fh, data_bytes, ocr_lang, 1, dpi, psm)
    kontrak = extract_contract_fields(full_quick, page1_quick)  # full_quick may be empty, but page1_quick used inside function
    # Decide if need wider extraction
    need_full = (not kontrak.get("nomor_kontrak") or not kontrak.get("tanggal_mulai_raw") or kontrak.get("nilai_kontrak") is None)
    if need_full or force_ocr:
        page1_full, full_text = cached_extraction(fh, data_bytes, ocr_lang, max_pages, dpi, psm)
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
    page1, full_text = cached_extraction(fh, data_bytes, ocr_lang, max_pages, dpi, psm)
    ba = extract_ba_fields(full_text, page1)
    # validate BA vs contract period
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
    page1, full_text = cached_extraction(fh, data_bytes, ocr_lang, max_pages, dpi, psm)
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
                tol = float(tol_pct)/100.0
                invoice["amount_match"] = abs(inv_amt - contract_amt) <= (tol * contract_amt)
                invoice["amount_status"] = "MATCH" if invoice["amount_match"] else "NOT MATCH"
        except Exception:
            pass
    st.session_state["invoice"] = invoice
    safe_rerun()

st.markdown("---")

# Summary & export
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

# JSON export
all_data = {"kontrak": k, "ba": b, "invoice": inv}
st.download_button("Download JSON", json.dumps(all_data, ensure_ascii=False, indent=2).encode("utf-8"), file_name="three_way_matching.json")
st.download_button("Download summary (CSV)", df.to_csv(index=False).encode("utf-8"), file_name="three_way_summary.csv")

st.info("Tips: untuk kecepatan, gunakan OCR language 'ind', Max pages kecil (1-3), dan DPI 150-250. Jika halaman 1 adalah gambar, centang 'Force OCR'.")
