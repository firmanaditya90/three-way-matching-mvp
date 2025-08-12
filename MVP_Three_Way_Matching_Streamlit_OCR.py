# app.py
"""
Three-Way Matching — Full, fast-first, OCR fallback (Streamlit)
- Prioritize page1(no-OCR) for speed; fallback OCR only when needed.
- Extract: nomor_kontrak (page1-priority), tanggal_mulai (page1), tanggal_selesai (from duration), nilai_kontrak (Pasal Biaya Pelaksanaan Pekerjaan).
- Extract BA & Invoice and perform three-way matching.
- Cache per-file to avoid reprocessing.
"""

import streamlit as st
import io, re, hashlib
from datetime import timedelta
import dateparser
import pandas as pd

# OCR / PDF libraries
import pdfplumber
from pdf2image import convert_from_bytes
from PIL import Image
import pytesseract

# Page config
st.set_page_config(page_title="Three-Way Matching (Fast + OCR)", layout="wide")

# -------------------------
# Utilities
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
    return dateparser.parse(s, languages=["id","en"])

def normalize_amount_raw(s):
    if not s:
        return None
    s = str(s)
    s = s.replace("Rp", "").replace("rp", "").replace("IDR", "")
    s = s.replace(",-", "")
    s = re.sub(r"[^\d,\.]", "", s)
    # Indonesian thousands: dot; decimal: comma (handle both)
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

# -------------------------
# Text extraction helpers
# -------------------------
def ocr_pil_image(pil_img, ocr_lang, tconfig):
    try:
        return pytesseract.image_to_string(pil_img, lang=ocr_lang, config=tconfig)
    except Exception:
        try:
            return pytesseract.image_to_string(pil_img)
        except Exception:
            return ""

def extract_text_and_page1_from_bytes(bytes_data, ocr_lang="ind", max_pages=None, dpi=200, force_ocr=False, psm=6):
    """
    Returns (full_text, page1_text)
    Strategy:
      - Try pdfplumber.extract_text per page; if page text short OR force_ocr -> OCR that page only.
      - If pdfplumber fails entirely -> use pdf2image convert_from_bytes + OCR pages.
      - max_pages limits work for speed (None => all)
    """
    tconfig = f"--psm {psm}"
    full_text = ""
    page1_text = ""

    if not force_ocr:
        try:
            with pdfplumber.open(io.BytesIO(bytes_data)) as pdf:
                pages = pdf.pages
                if len(pages) == 0:
                    raise RuntimeError("Empty PDF")
                pages_to_iter = pages if not max_pages else pages[:max_pages]
                for i, p in enumerate(pages_to_iter):
                    txt = p.extract_text() or ""
                    if len(txt.strip()) < 20 or force_ocr:
                        # OCR this page only
                        try:
                            pil = p.to_image(resolution=dpi).original
                            ocr_txt = ocr_pil_image(pil, ocr_lang, tconfig)
                            txt = ocr_txt or txt
                        except Exception:
                            pass
                    full_text += "\n" + (txt or "")
                    if i == 0:
                        page1_text = txt or ""
            return clean_text(full_text), clean_text(page1_text)
        except Exception:
            # fallback to full OCR
            pass

    # fallback full OCR via pdf2image
    try:
        pil_pages = convert_from_bytes(bytes_data, dpi=dpi)
        for i, pil in enumerate(pil_pages):
            try:
                ocr_txt = ocr_pil_image(pil, ocr_lang, tconfig)
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

@st.cache_data(show_spinner=False)
def extract_text_cached(file_hash: str, bytes_data: bytes, ocr_lang, max_pages, dpi, force_ocr, psm):
    return extract_text_and_page1_from_bytes(bytes_data, ocr_lang=ocr_lang, max_pages=max_pages, dpi=dpi, force_ocr=force_ocr, psm=psm)

def extract_from_upload(uploaded_file, ocr_lang="ind", max_pages=None, dpi=200, force_ocr=False, psm=6):
    """
    Accepts a Streamlit UploadedFile, returns (full_text, page1_text).
    Supports PDF, DOCX, and common images.
    """
    if uploaded_file is None:
        return "", ""
    name = uploaded_file.name.lower()
    data = uploaded_file.read()
    uploaded_file.seek(0)
    if name.endswith(".pdf"):
        fh = file_sha256_bytes(data)
        return extract_text_cached(fh, data, ocr_lang, max_pages, dpi, force_ocr, psm)
    if name.endswith((".docx", ".doc")):
        try:
            import docx
            doc = docx.Document(io.BytesIO(data))
            paras = [p.text for p in doc.paragraphs]
            txt = "\n".join(paras)
            return clean_text(txt), clean_text("\n".join(paras[:30]))
        except Exception:
            return "", ""
    if name.endswith((".png", ".jpg", ".jpeg", ".tiff", ".bmp")):
        try:
            pil = Image.open(io.BytesIO(data))
            tconfig = f"--psm {psm}"
            ocr_txt = ocr_pil_image(pil, ocr_lang, tconfig)
            return clean_text(ocr_txt), clean_text(ocr_txt)
        except Exception:
            return "", ""
    return "", ""

# -------------------------
# Domain extraction logic
# -------------------------
DATE_PATTERN_LONG = r"(\d{1,2}\s+(?:Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s*\d{2,4})"
DURATION_PATTERN = r"(\d{1,4})\s*(?:hari|kalender|calendar)"
# flexible nomor kontrak capture: common tokens + fallback broad pattern
NOMOR_PATTERNS = [
    r"\b(SPERJ\.[A-Z0-9\./\-]+)\b",
    r"\b(Sperj\.[A-Z0-9\./\-]+)\b",
    r"Nomor\s*Kontrak\s*[:\-\s]*([A-Z0-9\/\.\-\_]+)",
    r"NOMOR\s*[:\-\s]*([A-Z0-9\/\.\-\_]+)"
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
    # 1) nomor kontrak: try page1 then full
    for p in NOMOR_PATTERNS:
        v = safe_search(p, page1_text) or safe_search(p, full_text)
        if v:
            out["nomor_kontrak"] = v
            break
    # 2) tanggal mulai: prefer page1, else full
    d = safe_search(DATE_PATTERN_LONG, page1_text) or safe_search(DATE_PATTERN_LONG, full_text)
    if d:
        out["tanggal_mulai_raw"] = d
    # 3) duration days (from pasal jangka waktu)
    dur = safe_search(DURATION_PATTERN, full_text)
    if dur:
        try:
            out["duration_days"] = int(dur)
        except Exception:
            out["duration_days"] = None
    # 4) nilai kontrak from Pasal Biaya
    try:
        m = re.search(PASAL_BIAYA_HEADER, full_text, flags=re.IGNORECASE|re.DOTALL)
    except re.error:
        m = None
    if m:
        start = m.start()
        block = full_text[start:start+900]
        rp = safe_search(RP_NUMBER_PATTERN, block)
        if rp:
            out["nilai_kontrak_raw"] = rp
            out["nilai_kontrak"] = normalize_amount_raw(rp)
    # fallback: Total Biaya / Nilai Pekerjaan
    if not out["nilai_kontrak_raw"]:
        m2 = re.search(r"(Total\s+Biaya|Total\s*Nilai|Nilai\s+Pekerjaan).*?(Rp[\s\d\.,\-]+)", full_text, flags=re.IGNORECASE|re.DOTALL)
        if m2:
            rp = m2.group(2)
            out["nilai_kontrak_raw"] = rp
            out["nilai_kontrak"] = normalize_amount_raw(rp)
    # final fallback: first big Rp in doc
    if not out["nilai_kontrak_raw"]:
        anyrp = safe_search(RP_NUMBER_PATTERN, full_text)
        if anyrp:
            out["nilai_kontrak_raw"] = anyrp
            out["nilai_kontrak"] = normalize_amount_raw(anyrp)
    # 5) compute tanggal_selesai if duration & start present
    if out["duration_days"] and out["tanggal_mulai_raw"]:
        dt_start = parse_date_flexible(out["tanggal_mulai_raw"])
        if dt_start:
            dt_end = dt_start + timedelta(days=out["duration_days"])
            out["tanggal_selesai_raw"] = dt_end.strftime("%d %B %Y")
    return out

def extract_ba_fields(full_text, page1_text):
    out = {"tanggal_ba_raw": None, "tanggal_ba": None}
    v = safe_search(DATE_PATTERN_LONG, full_text) or safe_search(DATE_PATTERN_LONG, page1_text)
    if v:
        out["tanggal_ba_raw"] = v
        out["tanggal_ba"] = parse_date_flexible(v)
    return out

def extract_invoice_fields(full_text, page1_text):
    out = {"tanggal_invoice_raw": None, "tanggal_invoice": None, "dpp_raw": None, "ppn_raw": None, "total_raw": None, "total": None}
    v = safe_search(DATE_PATTERN_LONG, full_text) or safe_search(DATE_PATTERN_LONG, page1_text)
    if v:
        out["tanggal_invoice_raw"] = v
        out["tanggal_invoice"] = parse_date_flexible(v)
    # dpp/ppn/total attempts
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
    # fallback any Rp if still missing
    if not out["total_raw"]:
        anyrp = safe_search(RP_NUMBER_PATTERN, full_text)
        if anyrp:
            out["total_raw"] = anyrp
            out["total"] = normalize_amount_raw(anyrp)
    return out

# -------------------------
# UI & flow
# -------------------------
st.title("Three-Way Matching — Fast + OCR (MVP)")

with st.sidebar:
    st.header("Performance & OCR settings")
    # default fast choices: small max_pages, moderate dpi, ind-only language
    ocr_lang = st.selectbox("OCR language", ["ind","ind+eng","eng"], index=0)
    max_pages = st.number_input("Max pages to OCR (0 = all)", min_value=0, max_value=200, value=1)
    dpi = st.select_slider("OCR DPI (lower = faster)", options=[150,200,250,300], value=200)
    force_ocr = st.checkbox("Force OCR for all pages (use if PDF is scanned)", value=False)
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

# Process kontrak: fast-first strategy
if kontrak_file:
    bytes_data = kontrak_file.read()
    mp = None if max_pages==0 else max_pages
    # 1) Quick read: extract only page1 (no OCR) if possible
    full_text_quick, page1_quick = extract_text_and_page1_from_bytes(bytes_data, ocr_lang=ocr_lang, max_pages=1, dpi=dpi, force_ocr=False, psm=psm)
    kontrak = extract_contract_fields(full_text_quick, page1_quick)
    # If essential fields missing (nomor, tanggal, nilai), run fuller extraction (controlled by max_pages and force_ocr)
    need_full = False
    if not kontrak.get("nomor_kontrak") or not kontrak.get("tanggal_mulai_raw") or kontrak.get("nilai_kontrak") is None:
        need_full = True
    if need_full:
        full_text, page1 = extract_text_and_page1_from_bytes(bytes_data, ocr_lang=ocr_lang, max_pages=mp, dpi=dpi, force_ocr=force_ocr, psm=psm)
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
    full_text, page1 = extract_text_and_page1_from_bytes(bytes_data, ocr_lang=ocr_lang, max_pages=mp, dpi=dpi, force_ocr=force_ocr, psm=psm)
    ba = extract_ba_fields(full_text, page1)
    # validate BA vs contract
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
    full_text, page1 = extract_text_and_page1_from_bytes(bytes_data, ocr_lang=ocr_lang, max_pages=mp, dpi=dpi, force_ocr=force_ocr, psm=psm)
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
st.download_button("Download summary (CSV)", df.to_csv(index=False).encode("utf-8"), file_name="three_way_summary.csv")
