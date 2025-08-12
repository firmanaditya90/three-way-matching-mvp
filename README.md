# Three-Way Matching OCR (Streamlit)

Aplikasi web gratis untuk proses *Three-Way Matching* dokumen:
- Kontrak
- Berita Acara (BA)
- Invoice

Fitur:
- Ekstrak teks dari PDF / DOCX
- Otomatis OCR untuk dokumen hasil scan
- Validasi tanggal dan nilai sesuai kontrak
- Aman dari error regex

## Cara Deploy Gratis di Streamlit Cloud

1. Fork / clone repo ini.
2. Buka [Streamlit Cloud](https://streamlit.io/cloud).
3. Login dengan akun GitHub.
4. Klik **New app** → pilih repo ini.
5. Isi:
   - Branch: `main`
   - File: `app.py`
6. Klik **Deploy**.
7. Aplikasi langsung online gratis 🚀

## Requirements

Lihat file `requirements.txt`.

## Local Run (opsional)

```bash
pip install -r requirements.txt
streamlit run app.py
