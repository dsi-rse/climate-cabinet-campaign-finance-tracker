"""Save downloaded files and record where and when each came from

Collectors stream each download to disk with save_response, which hashes the
bytes as they are written. Archives (zip, 7z) are kept under ARCHIVE_DIRECTORY
with the retrieval time in the name, so a state that rebuilds its archive
daily (TX) doesn't overwrite the copy a run was built from. Each download is
then logged as one JSON line in DOWNLOAD_MANIFEST_NAME, in the state's raw data
directory, with the URL, retrieval time, the server's ETag and Last-Modified,
the size and sha256 of the file, and the files extracted from it.

Neither name matches any state's path_pattern, so standardize ignores them.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

from utils.provenance import (
    ARCHIVE_DIRECTORY,
    DOWNLOAD_MANIFEST_NAME,
    PARTIAL_DOWNLOAD_SUFFIX,
    describe_file,
    utc_now,
)

__all__ = [
    "ARCHIVE_DIRECTORY",
    "DOWNLOAD_MANIFEST_NAME",
    "add_extracted_files",
    "append_download_record",
    "archive_path",
    "read_download_records",
    "save_response",
]

logger = logging.getLogger(__name__)

_CHUNK_BYTES = 1024 * 1024


def archive_path(directory: Path, url: str, retrieved_at: str) -> Path:
    """Choose where to keep a downloaded archive

    Args:
        directory: The state's raw data directory.
        url: URL the archive was downloaded from. Its file name is kept.
        retrieved_at: ISO 8601 retrieval time, from utils.provenance.utc_now.

    Returns:
        <directory>/_archives/<name>.<YYYYMMDDTHHMMSSZ><extension>, for
        example _archives/TEC_CF_CSV.20261110T163225Z.zip. If that file
        already exists (two downloads in the same second), a counter is added
        before the extension so neither is overwritten.
    """
    name = Path(unquote(urlparse(url).path)).name or "download"
    stamp = datetime.fromisoformat(retrieved_at).strftime("%Y%m%dT%H%M%SZ")
    suffix = Path(name).suffix
    stem = name[: -len(suffix)] if suffix else name
    archive_directory = Path(directory) / ARCHIVE_DIRECTORY
    path = archive_directory / f"{stem}.{stamp}{suffix}"
    counter = 1
    while path.exists():
        path = archive_directory / f"{stem}.{stamp}-{counter}{suffix}"
        counter += 1
    return path


def save_response(
    response: requests.Response,
    path: Path,
    record_relative_to: Path,
    retrieved_at: str | None = None,
) -> dict:
    """Stream a response body to a file and describe the download

    The body is written to a temporary file next to path and renamed when
    complete, so an interrupted download never leaves a partial file at path
    or replaces an earlier file there. If the download fails, the temporary
    file is deleted and the error is raised again.

    Args:
        response: Response from requests, ideally made with stream=True so the
            body isn't held in memory.
        path: File to write.
        record_relative_to: Directory the record's paths are written relative
            to (the directory holding the download manifest).
        retrieved_at: When the request was made. Defaults to now.

    Returns:
        A download record: url (as requested), final_url (after redirects),
        retrieved_at, status, etag, last_modified, content_type,
        content_length (as the server reported it), path, size and sha256 (of
        the bytes written), and an empty "extracted" list for
        add_extracted_files.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = path.with_name(path.name + PARTIAL_DOWNLOAD_SUFFIX)
    digest = hashlib.sha256()
    size = 0
    try:
        with partial_path.open("wb") as file:
            for chunk in response.iter_content(chunk_size=_CHUNK_BYTES):
                file.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        partial_path.replace(path)
    except BaseException:
        partial_path.unlink(missing_ok=True)
        raise

    # after redirects, response.request is the last request; the first one in
    # the history is the URL the collector asked for
    first_response = response.history[0] if response.history else response
    request = first_response.request
    request_url = request.url if request is not None else None
    headers = response.headers
    return {
        "url": request_url or response.url,
        "final_url": response.url,
        "retrieved_at": retrieved_at or utc_now(),
        "status": response.status_code,
        "etag": headers.get("ETag"),
        "last_modified": headers.get("Last-Modified"),
        "content_type": headers.get("Content-Type"),
        "content_length": headers.get("Content-Length"),
        "path": _relative_label(path, record_relative_to),
        "size": size,
        "sha256": digest.hexdigest(),
        "extracted": [],
    }


def add_extracted_files(
    record: dict, paths: list[Path], record_relative_to: Path
) -> dict:
    """List the files extracted from a downloaded archive in its record

    Only files inside record_relative_to are listed. An archive can store a
    member name that is absolute or holds "..", and extractors write such a
    file inside the directory under a cleaned name, so a path outside the
    directory is not a file this download produced. It is logged and left
    out rather than read.

    Args:
        record: Download record from save_response.
        paths: Files extracted from the archive, as written to disk.
        record_relative_to: Directory the paths are written relative to.

    Returns:
        The record, with "extracted" holding each file's path, size and sha256.
    """
    directory = Path(record_relative_to).resolve()
    extracted = []
    for path in sorted(Path(path) for path in paths):
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(directory):
            logger.warning("%s is outside %s, so it is not listed", path, directory)
            continue
        extracted.append(describe_file(path, relative_to=record_relative_to))
    record["extracted"] = extracted
    return record


def append_download_record(directory: Path, record: dict) -> None:
    """Add a download record to a directory's download manifest

    Records are appended, so the manifest keeps every download over time.

    Args:
        directory: Directory holding the manifest (the state's raw data
            directory).
        record: Download record from save_response.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / DOWNLOAD_MANIFEST_NAME).open("a") as manifest:
        manifest.write(json.dumps(record) + "\n")


def read_download_records(directory: Path) -> list[dict]:
    """Read every record in a directory's download manifest

    Args:
        directory: Directory holding the manifest.

    Returns:
        The records, oldest first, or an empty list if there is no manifest.
    """
    path = Path(directory) / DOWNLOAD_MANIFEST_NAME
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _relative_label(path: Path, directory: Path) -> str:
    """Write a path relative to a directory when it is inside it

    Args:
        path: Path to write.
        directory: Directory to write it relative to.

    Returns:
        The relative path, or the full path if path is outside directory.
    """
    try:
        return Path(path).resolve().relative_to(Path(directory).resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()
