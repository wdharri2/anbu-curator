# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
import subprocess
from pathlib import Path

from curator.config import load_settings
from curator.db import connect


SUPPORTED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".heic",
    ".heif",
    ".webp",
    ".cr3",
}


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        while chunk := file.read(chunk_size):
            digest.update(chunk)

    return digest.hexdigest()


def extract_exif(path: Path) -> dict:
    result = subprocess.run(
        [
            "exiftool",
            "-json",
            "-n",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    parsed = json.loads(result.stdout)

    if not parsed:
        return {}

    return parsed[0]


def remote_archive_path(sha256: str, filename: str) -> str:
    suffix = Path(filename).suffix.lower()

    return f"{sha256[:2]}/{sha256[2:4]}/{sha256}{suffix}"


def ensure_remote_directory(
    archive_target: str,
    archive_root: str,
    relative_path: str,
) -> None:
    parent = str(Path(relative_path).parent)

    command = [
        "ssh",
        archive_target,
        "mkdir",
        "-p",
        f"{archive_root}/{parent}",
    ]

    subprocess.run(command, check=True)


def archive_original(
    source: Path,
    relative_archive_path: str,
) -> str:
    settings = load_settings()

    ensure_remote_directory(
        settings.archive_target,
        settings.archive_root,
        relative_archive_path,
    )

    destination = (
        f"{settings.archive_target}:"
        f"{settings.archive_root}/{relative_archive_path}"
    )

    subprocess.run(
        [
            "rsync",
            "-av",
            "--ignore-existing",
            str(source),
            destination,
        ],
        check=True,
    )

    return relative_archive_path


def ingest_file(
    path: Path,
    *,
    original_filename: str | None = None,
    source: str = "manual",
) -> dict:
    settings = load_settings()

    path = path.resolve()

    if not path.exists():
        raise FileNotFoundError(path)

    if not path.is_file():
        raise ValueError(f"Not a file: {path}")

    logical_filename = Path(
        original_filename or path.name
    ).name

    logical_suffix = Path(
        logical_filename
    ).suffix.lower()

    if logical_suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported extension: {logical_suffix}"
        )

    file_hash = sha256_file(path)

    with connect() as db:
        existing = db.execute(
            """
            SELECT id, filename, archive_path
            FROM photos
            WHERE sha256 = ?
            """,
            (file_hash,),
        ).fetchone()

        if existing:
            return {
                "status": "duplicate",
                "photo_id": existing["id"],
                "sha256": file_hash,
                "filename": existing["filename"],
                "archive_path": existing["archive_path"],
            }

    exif = extract_exif(path)

    width = exif.get("ImageWidth")
    height = exif.get("ImageHeight")

    captured_at = (
        exif.get("DateTimeOriginal")
        or exif.get("CreateDate")
        or exif.get("ModifyDate")
    )

    latitude = exif.get("GPSLatitude")
    longitude = exif.get("GPSLongitude")

    archive_path = remote_archive_path(
        file_hash,
        logical_filename,
    )

    archive_original(
        path,
        archive_path,
    )

    working_dir = settings.incoming / file_hash[:2]
    working_dir.mkdir(parents=True, exist_ok=True)

    working_path = working_dir / (
        f"{file_hash}{logical_suffix}"
    )

    if path != working_path:
        shutil.copy2(path, working_path)

    mime_type, _ = mimetypes.guess_type(path.name)

    normalized_exif = {
        key: value
        for key, value in exif.items()
        if isinstance(
            value,
            (str, int, float, bool, type(None)),
        )
    }

    normalized_exif["DetectedMIMEType"] = mime_type

    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO photos (
                sha256,
                filename,
                source,
                archive_path,
                work_path,
                captured_at,
                latitude,
                longitude,
                width,
                height,
                exif_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_hash,
                logical_filename,
                source,
                archive_path,
                str(working_path),
                captured_at,
                latitude,
                longitude,
                width,
                height,
                json.dumps(
                    normalized_exif,
                    ensure_ascii=False,
                ),
            ),
        )

        db.commit()

        photo_id = cursor.lastrowid

    return {
        "status": "ingested",
        "photo_id": photo_id,
        "sha256": file_hash,
        "filename": logical_filename,
        "archive_path": archive_path,
        "work_path": str(working_path),
        "captured_at": captured_at,
        "dimensions": [width, height],
    }


def ingest_directory(directory: Path) -> list[dict]:
    results = []

    for path in sorted(directory.iterdir()):
        if (
            path.is_file()
            and path.suffix.lower() in SUPPORTED_EXTENSIONS
        ):
            results.append(ingest_file(path))

    return results
