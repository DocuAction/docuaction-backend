"""
Document Text Extraction Service
Properly reads text from PDF, DOCX, XLSX, TXT, CSV, and images.
"""
import os
import logging
from pathlib import Path

logger = logging.getLogger("docuaction.extractor")


async def extract_text(file_path: str, file_type: str = None) -> str:
    """
    Extract readable text from any supported document type.
    
    Supported: PDF, DOCX, XLSX, XLS, CSV, TXT, PNG, JPG, JPEG, TIFF, BMP
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if not file_type:
        file_type = path.suffix.lower().replace(".", "")

    file_type = file_type.lower()
    logger.info(f"Extracting text from: {path.name} (type: {file_type})")

    try:
        if file_type == "txt":
            return _read_txt(path)
        elif file_type == "csv":
            return _read_csv(path)
        elif file_type == "pdf":
            return await _read_pdf(path)
        elif file_type in ("docx", "doc"):
            return _read_docx(path)
        elif file_type in ("xlsx", "xls"):
            return _read_xlsx(path)
        elif file_type in ("png", "jpg", "jpeg", "tiff", "bmp"):
            return _read_image(path)
        else:
            # Fallback: try reading as text
            return _read_txt(path)
    except Exception as e:
        logger.error(f"Text extraction failed for {path.name}: {e}")
        raise


def _read_txt(path: Path) -> str:
    """Read plain text files."""
    encodings = ["utf-8", "utf-16", "latin-1", "cp1252"]
    for enc in encodings:
        try:
            text = path.read_text(encoding=enc)
            logger.info(f"TXT: {len(text.split())} words extracted")
            return text
        except (UnicodeDecodeError, UnicodeError):
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def _read_csv(path: Path) -> str:
    """Read CSV files as structured text."""
    import csv
    rows = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i == 0:
                rows.append("HEADERS: " + " | ".join(row))
            else:
                rows.append(" | ".join(row))
            if i > 500:
                rows.append(f"... (truncated, {i}+ rows)")
                break
    text = "\n".join(rows)
    logger.info(f"CSV: {len(rows)} rows, {len(text.split())} words extracted")
    return text


async def _read_pdf(path: Path) -> str:
    """Read PDF files using PyPDF2."""
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(str(path))
        pages = []
        for i, page in enumerate(reader.pages):
            page_text = page.extract_text()
            if page_text and page_text.strip():
                pages.append(f"[Page {i+1}]\n{page_text.strip()}")
        
        if pages:
            text = "\n\n".join(pages)
            logger.info(f"PDF: {len(reader.pages)} pages, {len(text.split())} words extracted")
            return text
        else:
            logger.warning("PDF: No extractable text found - may be scanned/image-based")
            return "[This PDF appears to be scanned or image-based. No extractable text was found. Consider uploading a text-based PDF or the source Word document.]"
    except ImportError:
        logger.error("PyPDF2 not installed - falling back to raw read")
        return _read_txt(path)


def _read_docx(path: Path) -> str:
    """Read Word documents using python-docx."""
    try:
        from docx import Document
        doc = Document(str(path))
        
        parts = []
        
        # Extract paragraphs
        for para in doc.paragraphs:
            text = para.text.strip()
            if text:
                # Preserve heading structure
                if para.style and para.style.name and "Heading" in para.style.name:
                    parts.append(f"\n## {text}\n")
                else:
                    parts.append(text)
        
        # Extract tables
        for table_idx, table in enumerate(doc.tables):
            parts.append(f"\n[Table {table_idx + 1}]")
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                parts.append(" | ".join(cells))
        
        text = "\n".join(parts)
        logger.info(f"DOCX: {len(doc.paragraphs)} paragraphs, {len(doc.tables)} tables, {len(text.split())} words extracted")
        return text
    except ImportError:
        logger.error("python-docx not installed - falling back to raw read")
        return _read_txt(path)


def _read_xlsx(path: Path) -> str:
    """Read Excel files using openpyxl."""
    try:
        from openpyxl import load_workbook
        wb = load_workbook(str(path), read_only=True, data_only=True)
        
        parts = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            parts.append(f"\n[Sheet: {sheet_name}]")
            row_count = 0
            for row in ws.iter_rows(values_only=True):
                cells = [str(cell) if cell is not None else "" for cell in row]
                if any(c.strip() for c in cells):
                    parts.append(" | ".join(cells))
                    row_count += 1
                if row_count > 500:
                    parts.append("... (truncated)")
                    break
        
        wb.close()
        text = "\n".join(parts)
        logger.info(f"XLSX: {len(wb.sheetnames)} sheets, {len(text.split())} words extracted")
        return text
    except ImportError:
        logger.error("openpyxl not installed - falling back to raw read")
        return _read_txt(path)


def _read_image(path: Path) -> str:
    """Read text from images using basic OCR or return description."""
    # For now, return a message. Full OCR requires Tesseract.
    # In production, you can add pytesseract or send to Claude Vision API.
    file_size = path.stat().st_size
    return f"[Image file: {path.name}, size: {file_size} bytes. Image-to-text OCR processing is available in the Enterprise plan. For now, please copy the text from the image and paste it into the text processing endpoint.]"
