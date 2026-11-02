"""Validate durable production paths before opening the application's DB."""
from pathlib import Path

from sqlalchemy.engine import make_url

from app.core.config import settings
from app.core.security import get_encryption_key


def validate_runtime_configuration() -> None:
    if settings.APP_ENV != "production":
        return
    if not settings.APP_LOGIN_REQUIRED:
        raise ValueError("Production requires owner login")
    get_encryption_key()
    data = Path(settings.DATA_DIR)
    if not data.is_absolute() or not data.is_dir():
        raise ValueError("Production requires a mounted absolute DATA_DIR")
    data = data.resolve()
    for value in (settings.DATABASE_URL, settings.CHECKPOINT_DATABASE_URL):
        url = make_url(value)
        if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
            raise ValueError("This deployment requires SQLite inside DATA_DIR")
        path = Path(url.database)
        if not path.is_absolute() or not path.resolve().is_relative_to(data):
            raise ValueError("Production database must be inside DATA_DIR")
    uploads = Path(settings.UPLOAD_DIR)
    if not uploads.is_absolute() or not uploads.resolve().is_relative_to(data):
        raise ValueError("Production uploads must be inside DATA_DIR")
