# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageOps

from curator.db import connect


PROXY_MAX_EDGE = 2560
PROXY_JPEG_QUALITY = 90


def utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def sha256_file(
    path: Path,
) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as fh:
        while True:
            chunk = fh.read(
                1024 * 1024
            )

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def load_photo(
    photo_id: int,
) -> dict:
    with connect() as db:
        row = db.execute(
            """
            SELECT
                id,
                sha256,
                filename,
                archive_path,
                work_path
            FROM photos
            WHERE id = ?
            """,
            (photo_id,),
        ).fetchone()

    if not row:
        raise ValueError(
            f"Photo {photo_id} does not exist"
        )

    return dict(row)


def archive_config() -> tuple[str, str, str]:
    load_dotenv(
        ".env",
        override=True,
    )

    return (
        os.environ[
            "CURATOR_ARCHIVE_HOST"
        ],
        os.environ[
            "CURATOR_ARCHIVE_USER"
        ],
        os.environ[
            "CURATOR_ARCHIVE_ROOT"
        ],
    )


def remote_archive_path(
    photo: dict,
) -> str:
    archive_path = photo[
        "archive_path"
    ]

    if not archive_path:
        raise ValueError(
            f"Photo {photo['id']} has no archive_path"
        )

    path = Path(
        archive_path
    )

    if path.is_absolute():
        return str(path)

    _, _, root = archive_config()

    return str(
        Path(root)
        / path
    )


def run_ssh(
    command: str,
) -> subprocess.CompletedProcess:
    host, user, _ = archive_config()

    return subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            f"{user}@{host}",
            command,
        ],
        check=True,
        text=True,
        capture_output=True,
    )


def remote_sha256(
    photo: dict,
) -> str:
    remote = remote_archive_path(
        photo
    )

    command = (
        "sha256sum -- "
        + shlex.quote(remote)
    )

    result = run_ssh(
        command
    )

    value = (
        result.stdout
        .strip()
        .split()[0]
    )

    if len(value) != 64:
        raise RuntimeError(
            "Invalid remote SHA-256 response"
        )

    return value


def record_error(
    photo_id: int,
    message: str,
) -> None:
    with connect() as db:
        db.execute(
            """
            INSERT INTO photo_storage_state (
                photo_id,
                last_error,
                updated_at
            )
            VALUES (?, ?, CURRENT_TIMESTAMP)

            ON CONFLICT(photo_id)
            DO UPDATE SET
                last_error=excluded.last_error,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                photo_id,
                message[:4000],
            ),
        )

        db.commit()


def verify_archive(
    photo_id: int,
) -> dict:
    photo = load_photo(
        photo_id
    )

    try:
        remote_hash = remote_sha256(
            photo
        )

        expected = photo[
            "sha256"
        ]

        if remote_hash != expected:
            raise RuntimeError(
                "Remote archive SHA-256 mismatch: "
                f"expected={expected} "
                f"remote={remote_hash}"
            )

        now = utc_now()

        with connect() as db:
            db.execute(
                """
                INSERT INTO photo_storage_state (
                    photo_id,
                    archive_verified_at,
                    archive_sha256,
                    last_error,
                    updated_at
                )
                VALUES (?, ?, ?, NULL, CURRENT_TIMESTAMP)

                ON CONFLICT(photo_id)
                DO UPDATE SET
                    archive_verified_at=
                        excluded.archive_verified_at,
                    archive_sha256=
                        excluded.archive_sha256,
                    last_error=NULL,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    photo_id,
                    now,
                    remote_hash,
                ),
            )

            db.commit()

        return {
            "photo_id": photo_id,
            "verified": True,
            "sha256": remote_hash,
        }

    except Exception as exc:
        record_error(
            photo_id,
            f"{type(exc).__name__}: {exc}",
        )

        raise


def proxy_root() -> Path:
    load_dotenv(
        ".env",
        override=True,
    )

    root = (
        Path(
            os.environ[
                "CURATOR_CACHE"
            ]
        )
        / "visual-proxies"
    )

    root.mkdir(
        parents=True,
        exist_ok=True,
    )

    return root


def ensure_proxy(
    photo_id: int,
) -> Path:
    photo = load_photo(
        photo_id
    )

    source = Path(
        photo["work_path"]
    )

    if not source.exists():
        with connect() as db:
            state = db.execute(
                """
                SELECT proxy_path
                FROM photo_storage_state
                WHERE photo_id = ?
                """,
                (photo_id,),
            ).fetchone()

        if (
            state
            and state["proxy_path"]
            and Path(
                state["proxy_path"]
            ).exists()
        ):
            return Path(
                state["proxy_path"]
            )

        raise FileNotFoundError(
            "Full working original is absent "
            "and no proxy exists."
        )

    output = (
        proxy_root()
        / (
            photo["sha256"]
            + ".jpg"
        )
    )

    if not output.exists():
        with Image.open(
            source
        ) as opened:
            image = ImageOps.exif_transpose(
                opened
            ).convert("RGB")

            image.thumbnail(
                (
                    PROXY_MAX_EDGE,
                    PROXY_MAX_EDGE,
                )
            )

            temp = output.with_suffix(
                ".tmp.jpg"
            )

            image.save(
                temp,
                "JPEG",
                quality=PROXY_JPEG_QUALITY,
                optimize=True,
            )

            os.replace(
                temp,
                output,
            )

    size = output.stat().st_size

    with connect() as db:
        db.execute(
            """
            INSERT INTO photo_storage_state (
                photo_id,
                proxy_path,
                proxy_bytes,
                last_error,
                updated_at
            )
            VALUES (?, ?, ?, NULL, CURRENT_TIMESTAMP)

            ON CONFLICT(photo_id)
            DO UPDATE SET
                proxy_path=excluded.proxy_path,
                proxy_bytes=excluded.proxy_bytes,
                last_error=NULL,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                photo_id,
                str(output),
                size,
            ),
        )

        db.commit()

    return output


def visual_path(
    photo_id: int,
) -> Path:
    photo = load_photo(
        photo_id
    )

    work = Path(
        photo["work_path"]
    )

    if work.exists():
        return work

    with connect() as db:
        state = db.execute(
            """
            SELECT proxy_path
            FROM photo_storage_state
            WHERE photo_id = ?
            """,
            (photo_id,),
        ).fetchone()

    if (
        state
        and state["proxy_path"]
    ):
        proxy = Path(
            state["proxy_path"]
        )

        if proxy.exists():
            return proxy

    raise FileNotFoundError(
        f"No local visual file for photo {photo_id}"
    )


def safe_evict(
    photo_id: int,
) -> dict:
    photo = load_photo(
        photo_id
    )

    work = Path(
        photo["work_path"]
    )

    if not work.exists():
        return {
            "photo_id": photo_id,
            "evicted": False,
            "reason": "already_absent",
        }

    local_hash = sha256_file(
        work
    )

    if local_hash != photo["sha256"]:
        raise RuntimeError(
            "Local working original does not "
            "match database SHA-256."
        )

    verify_archive(
        photo_id
    )

    proxy = ensure_proxy(
        photo_id
    )

    if not proxy.exists():
        raise RuntimeError(
            "Proxy creation failed."
        )

    original_bytes = (
        work.stat().st_size
    )

    work.unlink()

    with connect() as db:
        db.execute(
            """
            INSERT INTO photo_storage_state (
                photo_id,
                work_evicted_at,
                last_error,
                updated_at
            )
            VALUES (?, ?, NULL, CURRENT_TIMESTAMP)

            ON CONFLICT(photo_id)
            DO UPDATE SET
                work_evicted_at=
                    excluded.work_evicted_at,
                last_error=NULL,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                photo_id,
                utc_now(),
            ),
        )

        db.commit()

    return {
        "photo_id": photo_id,
        "evicted": True,
        "original_bytes_freed": (
            original_bytes
        ),
        "proxy_path": str(proxy),
        "proxy_bytes": (
            proxy.stat().st_size
        ),
    }


def restore_original(
    photo_id: int,
) -> dict:
    photo = load_photo(
        photo_id
    )

    work = Path(
        photo["work_path"]
    )

    if work.exists():
        current_hash = sha256_file(
            work
        )

        if current_hash != photo["sha256"]:
            raise RuntimeError(
                "Existing work file has "
                "incorrect SHA-256."
            )

        return {
            "photo_id": photo_id,
            "restored": False,
            "reason": "already_present",
        }

    verify_archive(
        photo_id
    )

    remote = remote_archive_path(
        photo
    )

    host, user, _ = archive_config()

    work.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp = work.with_name(
        work.name + ".restore-part"
    )

    if temp.exists():
        temp.unlink()

    subprocess.run(
        [
            "rsync",
            "-a",
            "--partial",
            f"{user}@{host}:{remote}",
            str(temp),
        ],
        check=True,
    )

    restored_hash = sha256_file(
        temp
    )

    if restored_hash != photo["sha256"]:
        temp.unlink(
            missing_ok=True
        )

        raise RuntimeError(
            "Restored original SHA-256 mismatch."
        )

    os.replace(
        temp,
        work,
    )

    with connect() as db:
        db.execute(
            """
            INSERT INTO photo_storage_state (
                photo_id,
                work_restored_at,
                last_error,
                updated_at
            )
            VALUES (?, ?, NULL, CURRENT_TIMESTAMP)

            ON CONFLICT(photo_id)
            DO UPDATE SET
                work_restored_at=
                    excluded.work_restored_at,
                last_error=NULL,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                photo_id,
                utc_now(),
            ),
        )

        db.commit()

    return {
        "photo_id": photo_id,
        "restored": True,
        "work_path": str(work),
        "sha256": restored_hash,
    }
