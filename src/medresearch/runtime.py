from contextlib import contextmanager

from filelock import FileLock, Timeout

from .config import ResearchError, Settings
from .service import Service


@contextmanager
def workspace(settings: Settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = FileLock(settings.data_dir / "workspace.lock", timeout=0)
    try:
        lock.acquire()
    except Timeout:
        raise ResearchError("Workspace already in use. Stop the other CLI/server, or use its API.") from None
    try:
        service = Service(settings)
        service.store.recover()
        yield service
    finally:
        lock.release()
