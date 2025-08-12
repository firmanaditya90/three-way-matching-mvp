"""
MVP Three-Way Matching (Streamlit) — OCR-enabled

Place this file in a GitHub repo and deploy to Streamlit Community Cloud (or Hugging Face Spaces).
Filename: MVP_Three_Way_Matching_Streamlit_OCR.py
"""

import streamlit as st
import pdfplumber
import docx
import re
from datetime import datetime
import dateparser
import pandas as pd
import io

# OCR & PDF->image
import pytesseract
from pdf2image import convert_from_bytes
from PIL import Image

st.set_page_config(page_title="Three-Way Matching (OCR)", layout="wide")

# -------------------- Utilities --------------------

def extract_text_from_pdf_bytes(file_bytes, ocr_lang='ind+eng', use_ocr=True, max_pages=10):
    """Try to extract text using pdfplumber first. If empty and use_ocr=True -> convert pages to images and OCR."""
    text = ""
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for i, p in enumerate(pdf.pages):
                if max_pages and i >= max_pages:
                    break
                page_text = p.extract_text() or ""
                text += page_text + "\n"
    except Exception:
        text = ""

    if text.strip():
        return text, {'ocr_used': False}

    # fallback: OCR via pdf2image + pytesseract
    if not use_ocr:
        return "", {'ocr_used': False}

    try:
        images = convert_from_bytes(file_bytes)
    except Exception as e:
        return "", {'ocr_used': False, 'error': str(e)}

    ocr_text = ""
    for i, img in enumerate(images):
        if max_pages and i >= max_pages:
            break
        try:
            page_txt = pytesseract.image_to_string(img, lang=ocr_lang)
            ocr_text += page_txt + "\n"
        except Exception:
            ocr_text += ""
    return ocr_text, {'ocr_used': True}

def extract_text_from_image_bytes(file_bytes, ocr_lang='ind+eng'):
    try:
        img = Image.open(io.BytesIO(file_bytes))
    except Exception:
        return ""
    try:
        return pytesseract.image_to_string(img, lang=ocr_lang)
    except Exception:
        return ""

def extract_text_from_docx_bytes(file_bytes):
    try:
        doc = docx.Document(io.BytesIO(file_bytes))
        full_text = []
        for para in doc.paragraphs:
            full_text.append(para.text)
        return "\n".join(full_text)
    except Exception:
        return ""

def extract_text(uploaded_file, ocr_lang='ind+eng', use_ocr=True):
    if uploaded_file is None:
        return "", {}
    name = uploaded_file.name.lower()
    data = uploaded_file.read()
    if name.endswith('.pdf'):
        text, meta = extract_text_from_pdf_bytes(data, ocr_lang=ocr_lang, use_ocr=use_ocr)
        return text, meta
    if name.endswith('.docx') or name.endswith('.doc'):
        return extract_text_from_docx_bytes(data), {'ocr_used': False}
    if name.endswith(('.png', '.jpg', '.jpeg', '.tiff', '.bmp')):
        txt = extract_text_from_image_bytes(data, ocr_lang=ocr_lang)
        return txt, {'ocr_used': True}
    # fallback try decode
    try:
        return data.decode('utf-8', errors='ignore'), {'ocr_used': False}
    finally:
        uploaded_file.seek(0)

def find_first_regex(pattern, text, flags=re.IGNORECASE):
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None

def parse_date_flexible(text):
    if not text:
        return None
    dt = dateparser.parse(text, languages=['id', 'en'])
    return dt

def normalize_amount(s):
    if s is None:
        return None
    s = s.replace('Rp', '').replace('IDR', '')
    s = re.sub(r'[^0-9,\\.]', '', s)
    if s.count(',') == 1 and s.count('.') > 1:
        s = s.replace('.', '')
        s = s.replace(',', '.')
    else:
        s = s.replace(',', '')
    try:
        return float(s)
    except Exception:
        return None

# -------------------- Extraction rules (MVP) --------------------

CONTRACT_NUMBER_PATTERNS = [
    r'Nomor Kontrak[:\\s]*([A-Za-z0-9\\-/]+)',
    r'No\\. Kontrak[:\\s]*([A-Za-z0-9\\-/]+)',
]

CONTRACT_START_PATTERNS = [
    r'Tanggal Mulai[:\\s]*([\\d\\w ,.-]+)',
    r'Mulai[:\\s]*([\\d\\w ,.-]+)'
]

CONTRACT_END_PATTERNS = [
    r'Tanggal Selesai[:\\s]*([\\d\\w ,.-]+)',
    r'Berakhir[:\\s]*([\\d\\w ,.-]+)'
]

CONTRACT_VALUE_PATTERNS = [
    r'Nilai Pekerjaan[:\\s]*Rp\\s*([\\d., ]+)',
    r'Nilai[:\\s]*Rp\\s*([\\d., ]+)',
]

BA_DATE_PATTERNS = [
    r'Tanggal BA[:\\s]*([\\d\\w ,.-]+)',
    r'Tanggal Berita Acara[:\\s]*([\\d\\w ,.-]+)',
    r'Tanggal[:\\s]*([\\d\\w ,.-]+)'
]

INVOICE_DATE_PATTERNS = [
    r'Tanggal Invoice[:\\s]*([\\d\\w ,.-]+)',
    r'Tanggal Faktur[:\\s]*([\\d\\w ,.-]+)',
    r'Tanggal[:\\s]*([\\d\\w ,.-]+)'
]

INVOICE_DPP_PATTERNS = [
    r'DPP[:\\s]*Rp\\s*([\\d., ]+)',
    r'Dasar Pengenaan Pajak[:\\s]*Rp\\s*([\\d., ]+)',
]

INVOICE_PPN_PATTERNS = [
    r'PPN[:\\s]*Rp\\s*([\\d., ]+)',
    r'P\\.P\\.N[:\\s]*Rp\\s*([\\d., ]+)',
]

INVOICE_TOTAL_PATTERNS = [
    r'Total[:\\s]*Rp\\s*([\\d., ]+)',
    r'Jumlah[:\\s]*Rp\\s*([\\d., ]+)',
    r'Total Invoice[:\\s]*Rp\\s*([\\d., ]+)',
]

def extract_contract(text):
    if not text:
        return {}
    res = {}
    for p in CONTRACT_NUMBER_PATTERNS:
        val = find_first_regex(p, text)
        if val:
            res['nomor_kontrak'] = val
            break
    for p in CONTRACT_START_PATTERNS:
        val = find_first_regex(p, text)
        if val:
            res['tanggal_mulai_raw'] = val
            parsed = parse_date_flexible(val)
            res['tanggal_mulai'] = parsed
            break
    for p in CONTRACT_END_PATTERNS:
        val = find_first_regex(p, text)
        if val:
            res['tanggal_selesai_raw'] = val
            parsed = parse_date_flexible(val)
            res['tanggal_selesai'] = parsed
            break
    for p in CONTRACT_VALUE_PATTERNS:
        val = find_first_regex(p, text)
        if val:
            res['nilai_kontrak_raw'] = val
            res['nilai_kontrak'] = normalize_amount(val)
            break
    m = re.search(r'(Tata Cara Pembayaran[:\\s\\S]{0,400})', text, re.IGNORECASE)
    if m:
        res['tata_cara'] = m.group(1)
    else:
        res['tata_cara'] = None
    return res

def extract_ba(text):
    if not text:
        return {}
    res = {}
    for p in BA_DATE_PATTERNS:
        val = find_first_regex(p, text)
        if val:
            res['tanggal_ba_raw'] = val
            res['tanggal_ba'] = parse_date_flexible(val)
            break
    return res

def extract_invoice(text):
    if not text:
        return {}
    res = {}
    for p in INVOICE_DATE_PATTERNS:
        val = find_first_regex(p, text)
        if val:
            res['tanggal_invoice_raw'] = val
            res['tanggal_invoice'] = parse_date_flexible(val)
            break
    for p in INVOICE_DPP_PATTERNS:
        val = find_first_regex(p, text)
        if val:
            res['dpp_raw'] = val
            res['dpp'] = normalize_amount(val)
            break
    for p in INVOICE_PPN_PATTERNS:
        val = find_first_regex(p, text)
        if val:
            res['ppn_raw'] = val
            res['ppn'] = normalize_amount(val)
            break
    for p in INVOICE_TOTAL_PATTERNS:
        val = find_first_regex(p, text)
        if val:
            res['total_raw'] = val
            res['total'] = normalize_amount(val)
            break
    return res

# -------------------- Streamlit UI --------------------

st.title("Three-Way Matching — OCR-enabled MVP")
st.markdown("Upload kontrak / BA / Invoice. Jika dokumen adalah scan, app akan mencoba OCR (Tesseract).")

with st.sidebar:
    st.header('OCR Settings')
    ocr_enabled = st.checkbox('Enable OCR fallback (for scanned docs)', value=True)
    ocr_lang_choice = st.selectbox('OCR language', options=['ind+eng', 'ind', 'eng'], index=0)
    max_pages_ocr = st.number_input('Max pages to OCR (per PDF)', min_value=1, max_value=50, value=5)

col1, col2 = st.columns([1,2])

with col1:
    st.header("1) Upload Kontrak")
    contract_file = st.file_uploader("Upload kontrak (PDF / DOCX)", type=['pdf', 'docx'])
    if st.button('Clear kontrak'):
        st.session_state.pop('contract', None)

with col2:
    st.header("Hasil Ekstraksi Kontrak")
    if 'contract' in st.session_state:
        st.json(st.session_state['contract'], expanded=False)
    else:
        st.write("Belum ada kontrak diunggah")

if contract_file:
    txt, meta = extract_text(contract_file, ocr_lang=ocr_lang_choice, use_ocr=ocr_enabled)
    kontrak = extract_contract(txt)
    kontrak['_meta'] = meta
    st.session_state['contract'] = kontrak
    st.experimental_rerun()

st.markdown('---')

col3, col4 = st.columns([1,2])
with col3:
    st.header("2) Upload Berita Acara (BA)")
    ba_file = st.file_uploader("Upload BA (PDF / DOCX / IMG)", type=['pdf', 'docx', 'png', 'jpg', 'jpeg', 'tiff'])
    if st.button('Clear BA'):
        st.session_state.pop('ba', None)
with col4:
    st.header("Hasil Ekstraksi BA + Validasi")
    if 'ba' in st.session_state:
        st.json(st.session_state['ba'], expanded=False)
    else:
        st.write("Belum ada BA diunggah")

if ba_file:
    txt, meta = extract_text(ba_file, ocr_lang=ocr_lang_choice, use_ocr=ocr_enabled)
    ba = extract_ba(txt)
    ba['_meta'] = meta
    # validation
    if 'contract' in st.session_state and st.session_state['contract'].get('tanggal_mulai') and st.session_state['contract'].get('tanggal_selesai') and ba.get('tanggal_ba'):
        start = st.session_state['contract']['tanggal_mulai']
        end = st.session_state['contract']['tanggal_selesai']
        tba = ba['tanggal_ba']
        if start and end and tba:
            ba['in_contract_period'] = (start.date() <= tba.date() <= end.date())
            ba['status'] = 'MATCH' if ba['in_contract_period'] else 'NOT MATCH'
    st.session_state['ba'] = ba
    st.experimental_rerun()

st.markdown('---')

col5, col6 = st.columns([1,2])
with col5:
    st.header("3) Upload Invoice")
    invoice_file = st.file_uploader("Upload Invoice (PDF / DOCX / IMG)", type=['pdf', 'docx', 'png', 'jpg', 'jpeg', 'tiff'])
    if st.button('Clear Invoice'):
        st.session_state.pop('invoice', None)
with col6:
    st.header("Hasil Ekstraksi Invoice + Validasi")
    if 'invoice' in st.session_state:
        st.json(st.session_state['invoice'], expanded=False)
    else:
        st.write("Belum ada invoice diunggah")

if invoice_file:
    txt, meta = extract_text(invoice_file, ocr_lang=ocr_lang_choice, use_ocr=ocr_enabled)
    invoice = extract_invoice(txt)
    invoice['_meta'] = meta
    # date validation vs BA
    if 'ba' in st.session_state and st.session_state['ba'].get('tanggal_ba') and invoice.get('tanggal_invoice'):
        invoice['after_ba'] = invoice['tanggal_invoice'].date() >= st.session_state['ba']['tanggal_ba'].date()
        invoice['date_status'] = 'MATCH' if invoice['after_ba'] else 'NOT MATCH'
    # amount validation vs kontrak
    if 'contract' in st.session_state and st.session_state['contract'].get('nilai_kontrak') and invoice.get('total') is not None:
        invoice['amount_match'] = abs((invoice['total'] or 0) - (st.session_state['contract']['nilai_kontrak'] or 0)) < 1e-2
        invoice['amount_status'] = 'MATCH' if invoice['amount_match'] else 'NOT MATCH'
    st.session_state['invoice'] = invoice
    st.experimental_rerun()

st.markdown('---')

# -------------------- Summary & Export --------------------

st.header('Ringkasan Hasil')
rows = []
contract = st.session_state.get('contract', {})
ba = st.session_state.get('ba', {})
invoice = st.session_state.get('invoice', {})

rows.append({'Item': 'Kontrak - Nomor', 'Value': contract.get('nomor_kontrak')})
rows.append({'Item': 'Kontrak - Tanggal Mulai', 'Value': contract.get('tanggal_mulai').date().isoformat() if contract.get('tanggal_mulai') else None})
rows.append({'Item': 'Kontrak - Tanggal Selesai', 'Value': contract.get('tanggal_selesai').date().isoformat() if contract.get('tanggal_selesai') else None})
rows.append({'Item': 'Kontrak - Nilai', 'Value': contract.get('nilai_kontrak')})
rows.append({'Item': 'BA - Tanggal', 'Value': ba.get('tanggal_ba').date().isoformat() if ba.get('tanggal_ba') else None})
rows.append({'Item': 'BA - Status terhadap Kontrak', 'Value': ba.get('status')})
rows.append({'Item': 'Invoice - Tanggal', 'Value': invoice.get('tanggal_invoice').date().isoformat() if invoice.get('tanggal_invoice') else None})
rows.append({'Item': 'Invoice - Date Status (vs BA)', 'Value': invoice.get('date_status')})
rows.append({'Item': 'Invoice - Total', 'Value': invoice.get('total')})
rows.append({'Item': 'Invoice - Amount Status (vs Kontrak)', 'Value': invoice.get('amount_status')})

df = pd.DataFrame(rows)
st.table(df)

if st.button('Download summary (CSV)'):
    st.download_button('Download CSV', df.to_csv(index=False).encode('utf-8'), file_name='three_way_summary.csv')

st.markdown('\\n---\\n')
st.write('Selesai. Untuk hasil OCR lebih baik, siapkan scan dengan resolusi minimal 300 DPI, kontras baik, dan teks tidak miring. Untuk produksi pertimbangkan menyimpan data ke database dan menambahkan preprocessing gambar (OpenCV) atau ML-based layout parsing.')
