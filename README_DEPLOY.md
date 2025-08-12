# Three-Way Matching — OCR-enabled MVP

Files:
- MVP_Three_Way_Matching_Streamlit_OCR.py
- requirements.txt
- packages.txt

Deploy notes:
- For Streamlit Community Cloud, put `packages.txt` at repo root to install system packages via apt (tesseract, poppler).
- If deployment fails because of missing system packages, consider using Hugging Face Spaces with a Dockerfile.

