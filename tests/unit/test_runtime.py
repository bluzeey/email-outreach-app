"""Runtime validation uses synthetic configuration, keys and temporary paths."""

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import runtime, security

SYNTHETIC_KEY = base64.urlsafe_b64encode(b"r" * 32).decode()


@pytest.fixture
def production(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    config = SimpleNamespace(
        APP_ENV="production",
        APP_LOGIN_REQUIRED=True,
        DATA_DIR=str(data),
        DATABASE_URL=f"sqlite:///{data / 'outreach.db'}",
        CHECKPOINT_DATABASE_URL=f"sqlite:///{data / 'checkpoints.db'}",
        UPLOAD_DIR=str(data / "uploads"),
    )
    monkeypatch.setattr(runtime, "settings", config)
    monkeypatch.setattr(security.settings, "ENCRYPTION_KEY", SYNTHETIC_KEY)
    return config


def test_production_accepts_durable_data_paths_without_creating_files(production):
    assert runtime.validate_runtime_configuration() is None
    assert not list(Path(production.DATA_DIR).iterdir())


@pytest.mark.parametrize("environment", ["development", "test"])
def test_nonproduction_does_not_require_a_production_mount(production, monkeypatch, environment):
    production.APP_ENV = environment
    production.APP_LOGIN_REQUIRED = False
    production.DATA_DIR = "missing-relative-directory"
    production.DATABASE_URL = "sqlite:///:memory:"
    production.UPLOAD_DIR = "relative-uploads"
    monkeypatch.setattr(security.settings, "ENCRYPTION_KEY", "")
    assert runtime.validate_runtime_configuration() is None


def test_production_owner_login_cannot_be_disabled(production):
    production.APP_LOGIN_REQUIRED = False
    with pytest.raises(ValueError, match="requires owner login"):
        runtime.validate_runtime_configuration()


@pytest.mark.parametrize(
    "key", ["", "not-a-fernet-key", base64.urlsafe_b64encode(b"short").decode()]
)
def test_production_requires_a_valid_persistent_encryption_key(production, monkeypatch, key):
    monkeypatch.setattr(security.settings, "ENCRYPTION_KEY", key)
    with pytest.raises(ValueError, match="ENCRYPTION_KEY"):
        runtime.validate_runtime_configuration()


@pytest.mark.parametrize("kind", ["relative", "missing", "file"])
def test_data_directory_must_be_an_existing_absolute_directory(production, tmp_path, kind):
    if kind == "relative":
        production.DATA_DIR = "relative-data"
    elif kind == "missing":
        production.DATA_DIR = str(tmp_path / "missing-data")
    else:
        file = tmp_path / "not-a-directory"
        file.write_text("synthetic")
        production.DATA_DIR = str(file)
    with pytest.raises(ValueError, match="mounted absolute DATA_DIR"):
        runtime.validate_runtime_configuration()


@pytest.mark.parametrize("setting", ["DATABASE_URL", "CHECKPOINT_DATABASE_URL"])
@pytest.mark.parametrize(
    "value",
    [
        "sqlite:///:memory:",
        "sqlite://",
        "postgresql://synthetic@localhost/synthetic",
        "sqlite:///relative.db",
        "sqlite+aiosqlite:///:memory:",
    ],
)
def test_each_database_must_be_durable_sqlite_in_data_directory(production, setting, value):
    setattr(production, setting, value)
    with pytest.raises(ValueError, match="SQLite inside DATA_DIR|database must be inside DATA_DIR"):
        runtime.validate_runtime_configuration()


@pytest.mark.parametrize("setting", ["DATABASE_URL", "CHECKPOINT_DATABASE_URL"])
@pytest.mark.parametrize("kind", ["outside", "traversal", "prefix-sibling", "symlink"])
def test_database_cannot_escape_data_directory(production, tmp_path, setting, kind):
    data = Path(production.DATA_DIR)
    outside = tmp_path / "outside"
    outside.mkdir()
    if kind == "outside":
        destination = outside / "synthetic.db"
    elif kind == "traversal":
        destination = data / ".." / "outside" / "synthetic.db"
    elif kind == "prefix-sibling":
        destination = tmp_path / "data-other" / "synthetic.db"
    else:
        (data / "escape").symlink_to(outside, target_is_directory=True)
        destination = data / "escape" / "synthetic.db"
    setattr(production, setting, f"sqlite:///{destination}")
    with pytest.raises(ValueError, match="database must be inside DATA_DIR"):
        runtime.validate_runtime_configuration()


@pytest.mark.parametrize("kind", ["relative", "outside", "traversal", "prefix-sibling", "symlink"])
def test_uploads_cannot_escape_data_directory(production, tmp_path, kind):
    data = Path(production.DATA_DIR)
    outside = tmp_path / "outside"
    outside.mkdir()
    if kind == "relative":
        destination = Path("relative-uploads")
    elif kind == "outside":
        destination = outside
    elif kind == "traversal":
        destination = data / ".." / "outside"
    elif kind == "prefix-sibling":
        destination = tmp_path / "data-other" / "uploads"
    else:
        destination = data / "escape"
        destination.symlink_to(outside, target_is_directory=True)
    production.UPLOAD_DIR = str(destination)
    with pytest.raises(ValueError, match="uploads must be inside DATA_DIR"):
        runtime.validate_runtime_configuration()


def test_nested_absolute_paths_and_async_sqlite_are_supported(production):
    data = Path(production.DATA_DIR)
    production.DATABASE_URL = f"sqlite+aiosqlite:///{data / 'db' / 'outreach.db'}"
    production.CHECKPOINT_DATABASE_URL = f"sqlite+aiosqlite:///{data / 'checkpoints' / 'state.db'}"
    production.UPLOAD_DIR = str(data / "files" / "uploads")
    assert runtime.validate_runtime_configuration() is None
