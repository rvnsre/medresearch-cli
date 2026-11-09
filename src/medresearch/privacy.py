"""Best-effort masking. Human review is mandatory; this is not de-identification certification."""

import hashlib
import re

from .storage import encode

PATTERNS = [
    (r"(?im)^\s*(?:patient(?: name)?|name|address|dob|date of birth|mrn|patient id|"
     r"ad[ıi]|soyad[ıi]?|ünvan|doğum tarixi|фио|пациент|адрес|дата рождения)\s*[:=].*$", "[MASKED FIELD]"),
    (r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[EMAIL]"),
    (r"(?<!\w)(?:\+\d[\d ()-]{7,}\d)(?!\w)", "[PHONE]"),
    (r"\b\d{3}-\d{2}-\d{4}\b", "[IDENTIFIER]"),
    (r"\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})\b", "[DATE]"),
]


def mask(text: str) -> str:
    for pattern, replacement in PATTERNS:
        text = re.sub(pattern, replacement, text)
    return text


def digest(value: dict) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()
