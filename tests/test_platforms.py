import pytest

from medresearch.config import default_data_dir


@pytest.mark.parametrize("platform,suffix", [
    ("win32", "AppData/Local/MedResearch"),
    ("darwin", "Library/Application Support/MedResearch"),
    ("linux", ".local/share/MedResearch"),
])
def test_native_data_directory(monkeypatch, platform, suffix):
    for key in ("MEDRESEARCH_DATA_DIR", "LOCALAPPDATA", "XDG_DATA_HOME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("medresearch.config.sys.platform", platform)
    assert default_data_dir().as_posix().endswith(suffix)
