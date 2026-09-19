# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import json
import os
import shlex
import shutil
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from curator.db import connect
from curator.storage.lifecycle import (
    archive_config,
    remote_archive_path,
    run_ssh,
    sha256_file,
)


MIN_DURABLE_ORIGINAL_REPLICAS = 2

NORMAL_LIMIT = 70
CLEANUP_LIMIT = 80
AGGRESSIVE_LIMIT = 90


def now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def load_env() -> None:
    load_dotenv(
        ".env",
        override=True,
    )


def ensure_locations() -> dict[str, int]:
    load_env()

    archive_host, _, archive_root = (
        archive_config()
    )

    work_root = os.environ[
        "CURATOR_WORK_ROOT"
    ]

    proxy_root = str(
        Path(
            os.environ[
                "CURATOR_CACHE"
            ]
        )
        / "visual-proxies"
    )

    locations = [
        {
            "name": "local-work",
            "host_key": "local",
            "location_kind": "local_work",
            "root_path": work_root,
            "durability": "durable",
        },
        {
            "name": "archive-original",
            "host_key": archive_host,
            "location_kind": "remote_archive",
            "root_path": archive_root,
            "durability": "durable",
        },
        {
            "name": "local-proxy",
            "host_key": "local",
            "location_kind": "derived_cache",
            "root_path": proxy_root,
            "durability": "derived",
        },
    ]

    with connect() as db:
        for loc in locations:
            db.execute(
                """
                INSERT INTO storage_locations (
                    name,
                    host_key,
                    location_kind,
                    root_path,
                    durability,
                    enabled,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)

                ON CONFLICT(name)
                DO UPDATE SET
                    host_key=excluded.host_key,
                    location_kind=excluded.location_kind,
                    root_path=excluded.root_path,
                    durability=excluded.durability,
                    enabled=1,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    loc["name"],
                    loc["host_key"],
                    loc["location_kind"],
                    loc["root_path"],
                    loc["durability"],
                ),
            )

        db.commit()

        rows = db.execute(
            """
            SELECT id, name
            FROM storage_locations
            WHERE enabled=1
            """
        ).fetchall()

    return {
        row["name"]: row["id"]
        for row in rows
    }


def upsert_replica(
    *,
    photo_id: int,
    location_id: int,
    replica_type: str,
    path: str,
    state: str,
    size: int | None,
    sha256: str | None,
    verified_at: str | None,
    error: str | None = None,
) -> None:
    with connect() as db:
        db.execute(
            """
            INSERT INTO storage_replicas (
                photo_id,
                location_id,
                replica_type,
                path,
                state,
                bytes,
                sha256,
                verified_at,
                last_seen_at,
                last_error,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)

            ON CONFLICT(
                photo_id,
                location_id,
                replica_type
            )
            DO UPDATE SET
                path=excluded.path,
                state=excluded.state,
                bytes=excluded.bytes,
                sha256=excluded.sha256,
                verified_at=excluded.verified_at,
                last_seen_at=excluded.last_seen_at,
                last_error=excluded.last_error,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                photo_id,
                location_id,
                replica_type,
                path,
                state,
                size,
                sha256,
                verified_at,
                now(),
                error,
            ),
        )

        db.commit()


def remote_original_info(
    photo: dict,
) -> dict:
    path = remote_archive_path(
        photo
    )

    command = (
        "set -e; "
        "sha256sum -- "
        + shlex.quote(path)
        + "; "
        "stat -c '%s' -- "
        + shlex.quote(path)
    )

    result = run_ssh(
        command
    )

    lines = [
        x.strip()
        for x in result.stdout.splitlines()
        if x.strip()
    ]

    if len(lines) < 2:
        raise RuntimeError(
            "Unexpected remote archive response"
        )

    digest = lines[0].split()[0]
    size = int(lines[1])

    return {
        "path": path,
        "sha256": digest,
        "bytes": size,
    }


def inventory_photo(
    photo_id: int,
    *,
    verify_remote: bool = True,
) -> dict:
    locations = ensure_locations()

    with connect() as db:
        row = db.execute(
            """
            SELECT
                id,
                filename,
                sha256,
                archive_path,
                work_path
            FROM photos
            WHERE id=?
            """,
            (photo_id,),
        ).fetchone()

        state = db.execute(
            """
            SELECT
                proxy_path,
                proxy_bytes
            FROM photo_storage_state
            WHERE photo_id=?
            """,
            (photo_id,),
        ).fetchone()

    if not row:
        raise ValueError(
            f"Photo {photo_id} missing"
        )

    photo = dict(row)

    result = {
        "photo_id": photo_id,
        "filename": photo["filename"],
        "replicas": [],
    }

    # Local full-resolution original
    work = Path(
        photo["work_path"]
    )

    if work.exists():
        digest = sha256_file(
            work
        )

        good = (
            digest
            == photo["sha256"]
        )

        replica_state = (
            "verified"
            if good
            else "corrupt"
        )

        upsert_replica(
            photo_id=photo_id,
            location_id=locations[
                "local-work"
            ],
            replica_type="original",
            path=str(work),
            state=replica_state,
            size=work.stat().st_size,
            sha256=digest,
            verified_at=(
                now()
                if good
                else None
            ),
            error=(
                None
                if good
                else "SHA-256 mismatch"
            ),
        )

        result["replicas"].append(
            {
                "location": "local-work",
                "type": "original",
                "state": replica_state,
            }
        )

    else:
        upsert_replica(
            photo_id=photo_id,
            location_id=locations[
                "local-work"
            ],
            replica_type="original",
            path=str(work),
            state="absent",
            size=None,
            sha256=None,
            verified_at=None,
        )

    # Archive archive original
    try:
        if verify_remote:
            remote = remote_original_info(
                photo
            )

            good = (
                remote["sha256"]
                == photo["sha256"]
            )

            replica_state = (
                "verified"
                if good
                else "corrupt"
            )

            upsert_replica(
                photo_id=photo_id,
                location_id=locations[
                    "archive-original"
                ],
                replica_type="original",
                path=remote["path"],
                state=replica_state,
                size=remote["bytes"],
                sha256=remote["sha256"],
                verified_at=(
                    now()
                    if good
                    else None
                ),
                error=(
                    None
                    if good
                    else "SHA-256 mismatch"
                ),
            )

            result["replicas"].append(
                {
                    "location": "archive-original",
                    "type": "original",
                    "state": replica_state,
                }
            )

    except Exception as exc:
        try:
            remote_path = (
                remote_archive_path(
                    photo
                )
            )
        except Exception:
            remote_path = (
                photo["archive_path"]
                or "UNKNOWN"
            )

        upsert_replica(
            photo_id=photo_id,
            location_id=locations[
                "archive-original"
            ],
            replica_type="original",
            path=remote_path,
            state="error",
            size=None,
            sha256=None,
            verified_at=None,
            error=(
                f"{type(exc).__name__}: {exc}"
            ),
        )

        result["replicas"].append(
            {
                "location": "archive-original",
                "type": "original",
                "state": "error",
            }
        )

    # Local visual proxy
    if (
        state
        and state["proxy_path"]
    ):
        proxy = Path(
            state["proxy_path"]
        )

        if proxy.exists():
            upsert_replica(
                photo_id=photo_id,
                location_id=locations[
                    "local-proxy"
                ],
                replica_type="proxy",
                path=str(proxy),
                state="present",
                size=proxy.stat().st_size,
                sha256=None,
                verified_at=None,
            )

            result["replicas"].append(
                {
                    "location": "local-proxy",
                    "type": "proxy",
                    "state": "present",
                }
            )

    return result


def durable_original_count(
    photo_id: int,
) -> int:
    with connect() as db:
        row = db.execute(
            """
            SELECT COUNT(DISTINCT sl.id) AS c

            FROM storage_replicas sr

            JOIN storage_locations sl
              ON sl.id = sr.location_id

            WHERE sr.photo_id = ?
              AND sr.replica_type = 'original'
              AND sr.state = 'verified'
              AND sl.durability = 'durable'
              AND sl.enabled = 1
            """,
            (photo_id,),
        ).fetchone()

    return int(row["c"])


def photo_is_durably_protected(
    photo_id: int,
) -> bool:
    return (
        durable_original_count(photo_id)
        >= MIN_DURABLE_ORIGINAL_REPLICAS
    )



def inventory_all() -> list[dict]:
    ensure_locations()

    with connect() as db:
        rows = db.execute(
            """
            SELECT id
            FROM photos
            ORDER BY id
            """
        ).fetchall()

    results = []

    for index, row in enumerate(
        rows,
        start=1,
    ):
        photo_id = row["id"]

        print(
            f"[{index}/{len(rows)}] "
            f"inventory photo {photo_id}"
        )

        results.append(
            inventory_photo(
                photo_id
            )
        )

    return results


def local_capacity() -> dict:
    load_env()

    root = Path(
        os.environ[
            "CURATOR_WORK_ROOT"
        ]
    )

    usage = shutil.disk_usage(
        root
    )

    used_percent = (
        usage.used
        / usage.total
        * 100
    )

    return {
        "name": "local-work",
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
        "used_percent": used_percent,
    }


def remote_capacity() -> dict:
    _, _, root = archive_config()

    command = (
        "df -B1 --output=size,used,avail,pcent "
        + shlex.quote(root)
        + " | tail -1"
    )

    result = run_ssh(
        command
    )

    fields = result.stdout.split()

    if len(fields) != 4:
        raise RuntimeError(
            "Unexpected remote df output: "
            + result.stdout
        )

    total = int(fields[0])
    used = int(fields[1])
    free = int(fields[2])
    percent = float(
        fields[3].rstrip("%")
    )

    return {
        "name": "archive-original",
        "total": total,
        "used": used,
        "free": free,
        "used_percent": percent,
    }


def pressure_level(
    percent: float,
) -> str:
    if percent < NORMAL_LIMIT:
        return "normal"

    if percent < CLEANUP_LIMIT:
        return "watch"

    if percent < AGGRESSIVE_LIMIT:
        return "cleanup"

    return "emergency"


def headroom_to_percent(
    capacity: dict,
    target: float = 80.0,
) -> int:
    target_used = int(
        capacity["total"]
        * (
            target / 100
        )
    )

    return max(
        0,
        target_used
        - capacity["used"],
    )


def report() -> dict:
    with connect() as db:
        total_photos = db.execute(
            """
            SELECT COUNT(*) AS c
            FROM photos
            """
        ).fetchone()["c"]

        rows = db.execute(
            """
            SELECT
                p.id,
                COUNT(
                    DISTINCT CASE
                        WHEN sr.replica_type='original'
                         AND sl.durability='durable'
                         AND sr.state='verified'
                        THEN sl.id
                    END
                ) AS durable_count

            FROM photos p

            LEFT JOIN storage_replicas sr
              ON sr.photo_id=p.id

            LEFT JOIN storage_locations sl
              ON sl.id=sr.location_id

            GROUP BY p.id
            ORDER BY p.id
            """
        ).fetchall()

        derived_bytes = db.execute(
            """
            SELECT COALESCE(
                SUM(sr.bytes),
                0
            ) AS b
            FROM storage_replicas sr
            JOIN storage_locations sl
              ON sl.id=sr.location_id
            WHERE sl.durability='derived'
              AND sr.state='present'
            """
        ).fetchone()["b"]

        local_original_bytes = db.execute(
            """
            SELECT COALESCE(
                SUM(sr.bytes),
                0
            ) AS b
            FROM storage_replicas sr
            JOIN storage_locations sl
              ON sl.id=sr.location_id
            WHERE sl.name='local-work'
              AND sr.replica_type='original'
              AND sr.state='verified'
            """
        ).fetchone()["b"]

        # A Local original is evictable only if
        # enough OTHER durable verified originals
        # would remain afterward.
        evictable = db.execute(
            """
            SELECT COUNT(*) AS c
            FROM (
                SELECT
                    p.id,
                    COUNT(
                        DISTINCT CASE
                            WHEN
                                sr.replica_type='original'
                                AND sl.durability='durable'
                                AND sr.state='verified'
                                AND sl.name != 'local-work'
                            THEN sl.id
                        END
                    ) AS other_durable

                FROM photos p

                LEFT JOIN storage_replicas sr
                  ON sr.photo_id=p.id

                LEFT JOIN storage_locations sl
                  ON sl.id=sr.location_id

                GROUP BY p.id
            )
            WHERE other_durable >= ?
            """,
            (
                MIN_DURABLE_ORIGINAL_REPLICAS,
            ),
        ).fetchone()["c"]

    protected = sum(
        1
        for row in rows
        if row["durable_count"]
        >= MIN_DURABLE_ORIGINAL_REPLICAS
    )

    underprotected = sum(
        1
        for row in rows
        if row["durable_count"]
        < MIN_DURABLE_ORIGINAL_REPLICAS
    )

    zero = sum(
        1
        for row in rows
        if row["durable_count"] == 0
    )

    local = local_capacity()
    remote = remote_capacity()

    local["pressure"] = pressure_level(
        local["used_percent"]
    )

    remote["pressure"] = pressure_level(
        remote["used_percent"]
    )

    local_headroom = headroom_to_percent(
        local
    )

    remote_headroom = headroom_to_percent(
        remote
    )

    effective_headroom = min(
        local_headroom,
        remote_headroom,
    )

    return {
        "minimum_durable_original_replicas": (
            MIN_DURABLE_ORIGINAL_REPLICAS
        ),
        "photos": total_photos,
        "protected_photos": protected,
        "underprotected_photos": underprotected,
        "zero_durable_photos": zero,
        "evictable_local_originals": evictable,
        "local_original_bytes": (
            local_original_bytes
        ),
        "derived_bytes": derived_bytes,
        "local": local,
        "archive": remote,
        "headroom_to_80_percent": {
            "local": local_headroom,
            "archive": remote_headroom,
            "effective_two_copy_ingest": (
                effective_headroom
            ),
        },
    }


def human_bytes(
    value: int,
) -> str:
    size = float(value)

    units = [
        "B",
        "KB",
        "MB",
        "GB",
        "TB",
    ]

    for unit in units:
        if size < 1024 or unit == units[-1]:
            return (
                f"{size:.1f} {unit}"
            )

        size /= 1024

    return f"{size:.1f} TB"


def print_report(
    data: dict,
) -> None:
    print(
        json.dumps(
            data,
            indent=2,
        )
    )

    print()
    print(
        "Storage summary:"
    )

    print(
        "  protected originals: "
        f"{data['protected_photos']}/"
        f"{data['photos']}"
    )

    print(
        "  underprotected originals: "
        f"{data['underprotected_photos']}"
    )

    print(
        "  Local: "
        f"{data['local']['used_percent']:.1f}% "
        f"({data['local']['pressure']})"
    )

    print(
        "  Archive: "
        f"{data['archive']['used_percent']:.1f}% "
        f"({data['archive']['pressure']})"
    )

    print(
        "  effective two-copy headroom "
        "before 80% threshold: "
        + human_bytes(
            data[
                "headroom_to_80_percent"
            ][
                "effective_two_copy_ingest"
            ]
        )
    )
