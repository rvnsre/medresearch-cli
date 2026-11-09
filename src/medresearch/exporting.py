"""Atomic private report exports: no partial replacement or accidentally published records."""
import os
import tempfile
from pathlib import Path

from .config import private_path


def write_report(path: Path, text: str) -> None:
    path = private_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".medresearch-", suffix=".tmp", delete=False) as target:
            temp_path = Path(target.name)
            target.write(text)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
