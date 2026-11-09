from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


class ResearchError(Exception):
    """A safe, user-facing failure. Never include patient text or credentials."""


def check_url(value: str, *, local: bool = False) -> str:
    if any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise ResearchError("Service URL must not contain whitespace or control characters.")
    try:
        url = urlsplit(value)
        _ = url.port  # Validate malformed or out-of-range ports without exposing the supplied URL.
    except ValueError:
        raise ResearchError("Invalid service URL or port.") from None
    if url.username or url.password or url.query or url.fragment:
        raise ResearchError("Service URL must not contain credentials, query, or fragment.")
    if local:
        if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ResearchError("Ollama URL must use HTTP on a loopback host.")
    elif url.scheme != "https" or not url.hostname:
        raise ResearchError("External AI URL must use HTTPS.")
    return value.rstrip("/")


def context_setting() -> int:
    try:
        value = int(os.getenv("MEDRESEARCH_OLLAMA_CONTEXT", "32768"))
        if not 1024 <= value <= 262144:
            raise ValueError
        return value
    except ValueError:
        raise ResearchError("MEDRESEARCH_OLLAMA_CONTEXT must be an integer from 1024 to 262144.") from None


def private_path(path: Path) -> Path:
    path = path.resolve()
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "pyproject.toml").exists() and path.is_relative_to(source_root):
        raise ResearchError("Store private case data outside the source checkout.")
    if any((parent / ".git").exists() for parent in (path, *path.parents)):
        raise ResearchError("Store private case data outside any Git checkout.")
    return path


def default_data_dir() -> Path:
    override = os.getenv("MEDRESEARCH_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        return Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "MedResearch"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/MedResearch"
    xdg = Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    if not xdg.is_absolute():
        xdg = Path.home() / ".local/share"
    return xdg / "MedResearch"


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=default_data_dir)
    ollama_url: str = field(default_factory=lambda: os.getenv("MEDRESEARCH_OLLAMA_URL", "http://127.0.0.1:11434"))
    model: str = field(default_factory=lambda: os.getenv("MEDRESEARCH_MODEL", ""))
    ollama_context: int = field(default_factory=context_setting)
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
        if type(self.ollama_context) is not int or not 1024 <= self.ollama_context <= 262144:
            raise ResearchError("Ollama context must be an integer from 1024 to 262144.")
        object.__setattr__(self, "data_dir", private_path(self.data_dir))
        object.__setattr__(self, "ollama_url", check_url(self.ollama_url, local=True))
        if self.external_url:
            object.__setattr__(self, "external_url", check_url(self.external_url))
