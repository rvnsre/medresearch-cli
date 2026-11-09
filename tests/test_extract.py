import io

import pytest
from PIL import Image
from pypdf import PdfWriter

from medresearch.config import ResearchError
from medresearch.extract import extract


def test_utf8_and_invalid_files(settings):
    assert extract("a.txt", "Laboratoriya: nəticə\nРезультат".encode(), settings)[0].number == 1
    for name, data in [("a.txt", b""), ("a.txt", b"\xff"), ("a.exe", b"text"), ("a.pdf", b"bad PDF")]:
        with pytest.raises(ResearchError):
            extract(name, data, settings)


def test_image_ocr_preserves_warning(settings, monkeypatch):
    image = Image.new("RGB", (300, 100), "white")
    data = io.BytesIO()
    image.save(data, format="PNG")
    monkeypatch.setattr("medresearch.extract.pytesseract.get_languages", lambda config: ["eng"])
    monkeypatch.setattr("medresearch.extract.pytesseract.image_to_string", lambda *a, **k: "Synthetic report")
    page = extract("photo.png", data.getvalue(), settings)[0]
    assert page.method == "ocr" and page.text == "Synthetic report"
    assert any("aze, rus" in warning for warning in page.warnings)


def test_pdf_scanned_pages_and_ocr_unavailable(settings, monkeypatch):
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=100)
    writer.add_blank_page(width=300, height=100)
    data = io.BytesIO()
    writer.write(data)

    def missing(*args, **kwargs):
        raise OSError("Missing executable")

    monkeypatch.setattr("medresearch.extract.pytesseract.get_languages", missing)
    pages = extract("scan.pdf", data.getvalue(), settings)
    assert [p.number for p in pages] == [1, 2]
    assert all(not p.text and p.warnings and p.method == "ocr" for p in pages)


def test_encrypted_pdf_rejected(settings):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("secret")
    data = io.BytesIO()
    writer.write(data)
    with pytest.raises(ResearchError, match="Encrypted"):
        extract("locked.pdf", data.getvalue(), settings)


def test_text_pdf_page_provenance(settings):
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=100)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({
        NameObject("/F1"): writer._add_object(font),
    })})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 10 50 Td (Synthetic laboratory report) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    data = io.BytesIO()
    writer.write(data)
    result = extract("text.pdf", data.getvalue(), settings)
    assert result[0].number == 1 and result[0].method == "text"
    assert "Synthetic laboratory report" in result[0].text
