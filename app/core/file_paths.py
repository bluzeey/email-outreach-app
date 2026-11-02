"""Private, generated file names and descriptor-based storage access."""

import os
import re
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote

from app.core.config import settings

UUID_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
UPLOAD_PATTERN = re.compile(rf"{UUID_PATTERN}\.(csv|xls|xlsx|xlsm)")
EXPORT_PATTERN = re.compile(rf"export_{UUID_PATTERN}_{UUID_PATTERN}\.(csv|json)")
EXPORT_TOKEN_PATTERN = re.compile(rf"({UUID_PATTERN})\.(csv|json)")


def canonical_uuid(value: str) -> str:
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("Invalid file identifier")
    return value


def upload_extension(filename: str | None) -> str:
    """Validate a display name; it is never used as the stored file name."""
    if not filename or len(filename) > 255:
        raise ValueError("A plain CSV or Excel filename is required")
    decoded = filename
    for _ in range(5):
        if (
            any(char in decoded for char in ("/", "\\", ":"))
            or any(ord(char) < 32 or ord(char) == 127 for char in decoded)
            or ".." in decoded
        ):
            raise ValueError("A plain CSV or Excel filename is required")
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    else:
        raise ValueError("Invalid encoded filename")
    extension = Path(filename).suffix.lower().lstrip(".")
    if extension not in {"csv", "xls", "xlsx", "xlsm"} or filename.startswith("."):
        raise ValueError("Only CSV and Excel files (.csv, .xls, .xlsx, .xlsm) are allowed")
    return extension


def upload_destination(extension: str) -> str:
    name = f"{uuid.uuid4()}.{extension}"
    if not UPLOAD_PATTERN.fullmatch(name):
        raise ValueError("Invalid upload extension")
    return str(Path(settings.UPLOAD_DIR).absolute() / name)


def upload_basename(destination: str) -> str:
    path = Path(destination).absolute()
    if path.parent != Path(settings.UPLOAD_DIR).absolute() or not UPLOAD_PATTERN.fullmatch(path.name):
        raise ValueError("Invalid upload destination")
    return path.name


def export_basename(campaign_id: str, token: str) -> str:
    canonical_uuid(campaign_id)
    match = EXPORT_TOKEN_PATTERN.fullmatch(token)
    if not match:
        raise ValueError("Invalid export identifier")
    canonical_uuid(match[1])
    return f"export_{campaign_id}_{token}"


@contextmanager
def storage_directory(*, create: bool = False):
    root = Path(settings.UPLOAD_DIR).absolute()
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Pin the configured directory; reject a symlink at the storage root.
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def create_private_file(name: str, *, binary: bool = False):
    if not (UPLOAD_PATTERN.fullmatch(name) or EXPORT_PATTERN.fullmatch(name)):
        raise ValueError("Invalid storage filename")
    with storage_directory(create=True) as directory:
        descriptor = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
        )
        try:
            options = {} if binary else {"encoding": "utf-8", "newline": ""}
            with os.fdopen(descriptor, "wb" if binary else "w", **options) as stream:
                yield stream
        except BaseException:
            # Only our exclusively created file is eligible for cleanup.
            try:
                os.unlink(name, dir_fd=directory)
            except FileNotFoundError:
                pass
            raise


def remove_private_upload(destination: str) -> None:
    name = upload_basename(destination)
    with storage_directory() as directory:
        try:
            os.unlink(name, dir_fd=directory)
        except FileNotFoundError:
            pass


def open_export(name: str):
    if not EXPORT_PATTERN.fullmatch(name):
        raise ValueError("Invalid export filename")
    with storage_directory() as directory:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("Export is not a private regular file")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise
