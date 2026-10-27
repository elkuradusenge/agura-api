"""Receiving an upload on disk and validating that it can be analysed."""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.analysis.dictionary import (
    DictionaryEntry,
    SurveyReadError,
    build_dictionary,
    read_metadata,
)
from app.config import Settings
from app.core.errors import ApiError, unprocessable

logger = logging.getLogger("agura.analysis.ingest")

ALLOWED_SUFFIXES = {".dta"}
CHUNK_BYTES = 1024 * 1024
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class StoredFile:
    path: Path
    relative_path: str
    size_bytes: int
    checksum: str
    original_filename: str


def sanitise_filename(raw: str) -> str:
    name = Path(str(raw).replace("\\", "/")).name
    name = SAFE_NAME.sub("-", name).strip("-.")
    if not name:
        name = "survey.dta"
    if len(name) > 120:
        suffix = Path(name).suffix
        name = name[: 120 - len(suffix)] + suffix
    return name


def upload_directory(settings: Settings) -> Path:
    directory = Path(settings.upload_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def ensure_free_space(directory: Path, minimum_bytes: int) -> None:
    free = shutil.disk_usage(directory).free
    if free < minimum_bytes:
        raise ApiError(
            507,
            "upload.no_disk_space",
            "errors.upload.no_disk_space",
            "Not enough free disk space to accept this file",
            {"free_bytes": free, "required_bytes": minimum_bytes},
        )


def save_upload(
    *,
    file_object,
    filename: str,
    settings: Settings,
    survey_id: uuid.UUID | None = None,
) -> StoredFile:
    safe_name = sanitise_filename(filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise unprocessable(
            "upload.invalid_type",
            "errors.upload.invalid_type",
            "Only Stata .dta files can be uploaded",
        )

    directory = upload_directory(settings)
    ensure_free_space(directory, settings.min_free_disk_bytes)

    identifier = (survey_id or uuid.uuid4()).hex
    target = directory / identifier / safe_name
    target.parent.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256()
    size = 0
    try:
        with target.open("wb") as destination:
            while True:
                block = file_object.read(CHUNK_BYTES)
                if not block:
                    break
                size += len(block)
                if size > settings.max_upload_bytes:
                    raise ApiError(
                        413,
                        "upload.too_large",
                        "errors.upload.too_large",
                        "The file is larger than the configured limit",
                        {"max_bytes": settings.max_upload_bytes},
                    )
                digest.update(block)
                destination.write(block)
    except ApiError:
        target.unlink(missing_ok=True)
        raise
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise ApiError(
            507,
            "upload.write_failed",
            "errors.upload.write_failed",
            "The file could not be written to disk",
        ) from exc

    if size == 0:
        target.unlink(missing_ok=True)
        raise unprocessable(
            "upload.empty_file",
            "errors.upload.empty_file",
            "The uploaded file is empty",
        )

    return StoredFile(
        path=target,
        relative_path=str(target.relative_to(directory)),
        size_bytes=size,
        checksum=digest.hexdigest(),
        original_filename=safe_name,
    )


def delete_stored_file(settings: Settings, relative_path: str) -> None:
    directory = upload_directory(settings)
    target = (directory / relative_path).resolve()
    if not str(target).startswith(str(directory)):
        logger.warning("Refusing to delete a path outside the upload directory: %s", relative_path)
        return
    target.unlink(missing_ok=True)
    parent = target.parent
    if parent != directory and parent.exists() and not any(parent.iterdir()):
        parent.rmdir()


@dataclass(frozen=True)
class ValidationResult:
    entries: list[DictionaryEntry]
    key_variables_present: list[str]
    missing_key_variables: list[str]
    row_count: int | None
    column_count: int


def validate_upload(
    path: Path, required_variables: list[str], encoding: str | None = None
) -> ValidationResult:
    try:
        meta = read_metadata(str(path), encoding)
    except SurveyReadError as exc:
        raise unprocessable(
            "upload.cannot_read",
            "errors.upload.cannot_read",
            "The file could not be opened as a Stata data file",
        ) from exc

    entries = build_dictionary(meta)
    available = {entry.variable_name for entry in entries}
    present = [name for name in required_variables if name.lower() in available]
    missing = [name for name in required_variables if name.lower() not in available]

    return ValidationResult(
        entries=entries,
        key_variables_present=present,
        missing_key_variables=missing,
        row_count=getattr(meta, "number_rows", None),
        column_count=len(entries),
    )
