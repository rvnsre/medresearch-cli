from __future__ import annotations

import io
import warnings
from pathlib import Path

import pypdfium2 as pdfium
import pytesseract
from PIL import Image, ImageOps
from pypdf import PdfReader

from .config import ResearchError, Settings
from .models import Page


def ocr(image: Image.Image, settings: Settings) -> tuple[str, list[str]]:
    pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
    try:
        available = set(pytesseract.get_languages(config=""))
        requested = settings.ocr_languages.split("+")
        used = [lang for lang in requested if lang in available]
        missing = [lang for lang in requested if lang not in available]
        notes = ["OCR text requires human verification, including numbers, units and negation."]
        if missing:
            notes.append("Missing OCR languages: " + ", ".join(missing))
        if not used:
            return "", ["No requested OCR language packs are installed."]
        text = pytesseract.image_to_string(image, lang="+".join(used), timeout=60).strip()
        if not text:
            notes.append("No readable text detected; no content was inferred.")
        return text, notes
    except (pytesseract.TesseractNotFoundError, RuntimeError, pytesseract.TesseractError, OSError):
        return "", ["Local OCR unavailable or timed out. Install Tesseract/language packs or enter verified text."]


def extract(name: str, content: bytes, settings: Settings) -> list[Page]:
    if not content or len(content) > settings.max_bytes:
        raise ResearchError("Upload must be non-empty and at most 20 MiB.")
    suffix = Path(name).suffix.lower()
    pages = []
    try:
        if suffix == ".txt":
            text = content.decode("utf-8-sig").strip()
            pages = [Page(number=1, text=text, warnings=[] if text else ["Empty text."])]
        elif suffix in {".jpg", ".jpeg", ".png"}:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as img:
                    if img.width * img.height > 30_000_000:
                        raise ResearchError("Image exceeds 30 megapixels.")
                    if img.format not in {"JPEG", "PNG"}:
                        raise ResearchError("Image content must be JPEG or PNG.")
                    text, notes = ocr(ImageOps.exif_transpose(img).convert("RGB"), settings)
                    pages = [Page(number=1, text=text, method="ocr", warnings=notes)]
        elif suffix == ".pdf":
            if not content.lstrip().startswith(b"%PDF-"):
                raise ResearchError("File content is not a PDF.")
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                raise ResearchError("Encrypted PDFs are unsupported; provide a decrypted copy.")
            if not 0 < len(reader.pages) <= settings.max_pages:
                raise ResearchError("PDF must contain 1–100 pages.")
            rendered = None
            try:
                for number, page in enumerate(reader.pages, 1):
                    text = (page.extract_text() or "").strip()
                    if text:
                        pages.append(Page(number=number, text=text))
                    else:
                        if rendered is None:
                            rendered = pdfium.PdfDocument(content)
                        render_page = rendered[number - 1]
                        try:
                            width, height = render_page.get_size()
                            if width * height * 4 > 30_000_000:
                                raise ResearchError("PDF page exceeds the rendering limit.")
                            bitmap = render_page.render(scale=2)
                            try:
                                image = bitmap.to_pil()
                                text, notes = ocr(image, settings)
                                pages.append(Page(number=number, text=text, method="ocr", warnings=notes))
                            finally:
                                bitmap.close()
                        finally:
                            render_page.close()
                    if sum(len(p.text) for p in pages) > settings.max_text_chars:
                        raise ResearchError("Document text exceeds 200,000 characters.")
            finally:
                if rendered is not None:
                    rendered.close()
        else:
            raise ResearchError("Supported files: UTF-8 TXT, PDF, JPEG and PNG.")
    except ResearchError:
        raise
    except Exception:
        raise ResearchError("Document could not be decoded. Check its format and integrity.") from None
    if sum(len(p.text) for p in pages) > settings.max_text_chars:
        raise ResearchError("Document text exceeds 200,000 characters.")
    return pages
