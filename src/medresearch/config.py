from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


class ResearchError(Exception):
    """A safe, user-facing failure. Never include patient text or credentials."""


def check_url(value: str, *, local: bool = False) -> str:
    url = urlsplit(value)
    if url.username or url.password or url.query or url.fragment:
        raise ResearchError("Service URL must not contain credentials, query, or fragment.")
    if local:
        if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ResearchError("Ollama URL must use HTTP on a loopback host.")
    elif url.scheme != "https" or not url.hostname:
        raise ResearchError("External AI URL must use HTTPS.")
    return value.rstrip("/")


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv(
        "MEDRESEARCH_DATA_DIR",
        str(Path(os.getenv("LOCALAPPDATA", str(Path.home() / ".local/share"))) / "MedResearch"),
    )))
    ollama_url: str = field(default_factory=lambda: os.getenv("MEDRESEARCH_OLLAMA_URL", "http://127.0.0.1:11434"))
    model: str = field(default_factory=lambda: os.getenv("MEDRESEARCH_MODEL", ""))
    external_url: str = field(default_factory=lambda: os.getenv("MEDRESEARCH_EXTERNAL_URL", ""))
    external_key: str = field(default_factory=lambda: os.getenv("MEDRESEARCH_EXTERNAL_KEY", ""))
    api_token: str = field(default_factory=lambda: os.getenv("MEDRESEARCH_API_TOKEN", ""))
    ocr_languages: str = field(default_factory=lambda: os.getenv("MEDRESEARCH_OCR_LANGUAGES", "aze+rus+eng"))
    tesseract_cmd: str = field(default_factory=lambda: os.getenv("MEDRESEARCH_TESSERACT_CMD", "tesseract"))
    max_bytes: int = 20 * 1024 * 1024
    max_pages: int = 100
    max_text_chars: int = 200_000
    approval_ttl: int = 900

    def __post_init__(self):
        object.__setattr__(self, "data_dir", self.data_dir.resolve())
        source_root = Path(__file__).resolve().parents[2]
        if (source_root / "pyproject.toml").exists() and self.data_dir.is_relative_to(source_root):
            raise ResearchError("Store private case data outside the source checkout.")
        check_url(self.ollama_url, local=True)
        if self.external_url:
            check_url(self.external_url)
