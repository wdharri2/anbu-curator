# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from curator.config import load_settings
from curator.db import connect


SESSION_GAP_SECONDS = 300


def _scalar(
    db,
    query: str,
    params: tuple[Any, ...] = (),
) -> int:
    row = db.execute(query, params).fetchone()
    if row is None:
        return 0

    value = row[0]
    return 0 if value is None else value


def _parse_datetime(
    value: str | None,
) -> datetime | None:
    if not value:
        return None

    text = value.strip().replace("Z", "+00:00")

    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    return None


def _iso_or_none(
    dt: datetime | None,
) -> str | None:
    if dt is None:
        return None

    return dt.isoformat(
        timespec="seconds"
    )


def _disk_payload(
    path: str | Path,
    *,
    label: str,
) -> dict:
    resolved = Path(path)
    total, used, free = shutil.disk_usage(
        resolved
    )

    return {
        "label": label,
        "path": str(resolved),
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free,
        "used_percent": round(
            (used / total) * 100,
            1,
        ) if total else None,
    }


def _remote_root_payload(
    *,
    host: str | None,
    user: str | None,
) -> dict:
    if not host or not user:
        return {
            "label": "archive_host_root",
            "status": "unconfigured",
        }

    target = f"{user}@{host}"

    try:
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=3",
                target,
                "df -B1 / | awk 'NR==2 {print $2, $3, $4}'",
            ],
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )

        output = (
            result.stdout.strip()
            or result.stderr.strip()
        )

        if result.returncode != 0:
            return {
                "label": "archive_host_root",
                "status": "unavailable",
                "target": target,
                "error": output,
            }

        parts = output.split()

        if len(parts) != 3:
            return {
                "label": "archive_host_root",
                "status": "unavailable",
                "target": target,
                "error": f"unexpected_output={output!r}",
            }

        total, used, free = map(
            int,
            parts,
        )

        return {
            "label": "archive_host_root",
            "status": "ok",
            "target": target,
            "path": "/",
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
            "used_percent": round(
                (used / total) * 100,
                1,
            ) if total else None,
        }

    except Exception as exc:
        return {
            "label": "archive_host_root",
            "status": "unavailable",
            "target": target,
            "error": repr(exc),
        }


def _photo_stats(db) -> dict:
    total = _scalar(
        db,
        "SELECT COUNT(*) FROM photos",
    )

    analyzed = _scalar(
        db,
        """
        SELECT COUNT(*)
        FROM photos
        WHERE analysis_status = 'done'
        """,
    )

    pending = _scalar(
        db,
        """
        SELECT COUNT(*)
        FROM photos
        WHERE analysis_status IS NULL
           OR analysis_status NOT IN ('done', 'error')
        """,
    )

    errors = _scalar(
        db,
        """
        SELECT COUNT(*)
        FROM photos
        WHERE analysis_status = 'error'
        """,
    )

    with_crops = _scalar(
        db,
        """
        SELECT COUNT(*)
        FROM photos
        WHERE analysis_json LIKE '%"crop"%'
        """,
    )

    return {
        "total": total,
        "analyzed": analyzed,
        "pending": pending,
        "errors": errors,
        "with_crop_analysis": with_crops,
    }


def _analysis_timestamps(
    db,
) -> list[datetime]:
    rows = db.execute(
        """
        SELECT created_at
        FROM ai_runs
        WHERE stage = 'photo_analysis'
        ORDER BY created_at
        """
    ).fetchall()

    times: list[datetime] = []

    for row in rows:
        dt = _parse_datetime(
            row["created_at"]
        )
        if dt is not None:
            times.append(dt)

    return times


def _rolling_analysis_stats(
    db,
    *,
    window_minutes: int = 10,
) -> dict:
    count = _scalar(
        db,
        f"""
        SELECT COUNT(*)
        FROM ai_runs
        WHERE stage = 'photo_analysis'
          AND created_at >= datetime(
                'now',
                '-{window_minutes} minutes'
          )
        """,
    )

    photos_per_minute = round(
        count / window_minutes,
        2,
    )

    seconds_per_photo = round(
        (window_minutes * 60) / count,
        1,
    ) if count else None

    return {
        "window_minutes": window_minutes,
        "count": count,
        "photos_per_minute": photos_per_minute,
        "seconds_per_photo": seconds_per_photo,
    }


def _session_analysis_stats(
    db,
) -> dict:
    timestamps = _analysis_timestamps(
        db
    )

    if not timestamps:
        return {
            "count": 0,
            "started_at": None,
            "latest_at": None,
            "elapsed_minutes": 0.0,
            "photos_per_minute": None,
            "seconds_per_photo": None,
            "photos_per_hour": None,
        }

    session = [timestamps[-1]]

    for t in reversed(
        timestamps[:-1]
    ):
        gap = (
            session[-1] - t
        ).total_seconds()

        if gap > SESSION_GAP_SECONDS:
            break

        session.append(t)

    session.reverse()

    count = len(session)

    if count < 2:
        return {
            "count": count,
            "started_at": _iso_or_none(
                session[0]
            ),
            "latest_at": _iso_or_none(
                session[-1]
            ),
            "elapsed_minutes": 0.0,
            "photos_per_minute": None,
            "seconds_per_photo": None,
            "photos_per_hour": None,
        }

    seconds = (
        session[-1] - session[0]
    ).total_seconds()

    if seconds <= 0:
        return {
            "count": count,
            "started_at": _iso_or_none(
                session[0]
            ),
            "latest_at": _iso_or_none(
                session[-1]
            ),
            "elapsed_minutes": 0.0,
            "photos_per_minute": None,
            "seconds_per_photo": None,
            "photos_per_hour": None,
        }

    photos_per_minute = (
        (count - 1) / seconds
    ) * 60.0

    seconds_per_photo = (
        seconds / (count - 1)
    )

    return {
        "count": count,
        "started_at": _iso_or_none(
            session[0]
        ),
        "latest_at": _iso_or_none(
            session[-1]
        ),
        "elapsed_minutes": round(
            seconds / 60.0,
            1,
        ),
        "photos_per_minute": round(
            photos_per_minute,
            2,
        ),
        "seconds_per_photo": round(
            seconds_per_photo,
            1,
        ),
        "photos_per_hour": round(
            photos_per_minute * 60.0
        ),
    }


def _ingest_stats(db) -> dict:
    total = _scalar(
        db,
        "SELECT COUNT(*) FROM ingest_jobs",
    )

    pending = _scalar(
        db,
        """
        SELECT COUNT(*)
        FROM ingest_jobs
        WHERE status = 'pending'
        """,
    )

    processing = _scalar(
        db,
        """
        SELECT COUNT(*)
        FROM ingest_jobs
        WHERE status = 'processing'
        """,
    )

    done = _scalar(
        db,
        """
        SELECT COUNT(*)
        FROM ingest_jobs
        WHERE status = 'done'
        """,
    )

    errors = _scalar(
        db,
        """
        SELECT COUNT(*)
        FROM ingest_jobs
        WHERE status = 'error'
        """,
    )

    return {
        "total": total,
        "pending": pending,
        "processing": processing,
        "done": done,
        "errors": errors,
    }


def _upload_stats(db) -> dict:
    total = _scalar(
        db,
        """
        SELECT COUNT(*)
        FROM upload_sessions
        """,
    )

    receiving = _scalar(
        db,
        """
        SELECT COUNT(*)
        FROM upload_sessions
        WHERE status = 'receiving'
        """,
    )

    queued = _scalar(
        db,
        """
        SELECT COUNT(*)
        FROM upload_sessions
        WHERE status = 'queued'
        """,
    )

    return {
        "total": total,
        "receiving": receiving,
        "queued": queued,
    }


def _post_stats(db) -> dict:
    candidates = _scalar(
        db,
        """
        SELECT COUNT(*)
        FROM post_candidates
        WHERE status = 'candidate'
        """,
    )

    all_candidates = _scalar(
        db,
        """
        SELECT COUNT(*)
        FROM post_candidates
        """,
    )

    published = _scalar(
        db,
        """
        SELECT COUNT(*)
        FROM published_posts
        """,
    )

    return {
        "candidate_active": candidates,
        "candidate_total": all_candidates,
        "published_total": published,
    }


def build_overview() -> dict:
    settings = load_settings()

    with connect() as db:
        photos = _photo_stats(db)
        rolling = _rolling_analysis_stats(db)
        session = _session_analysis_stats(db)
        ingest = _ingest_stats(db)
        uploads = _upload_stats(db)
        posts = _post_stats(db)

    rate = (
        session["photos_per_minute"]
        or rolling["photos_per_minute"]
        or 0
    )

    eta_minutes = round(
        photos["pending"] / rate
    ) if rate else None

    storage = {
        "local_root": _disk_payload(
            "/",
            label="local_root",
        ),
        "data_root": _disk_payload(
            settings.data_root,
            label="data_root",
        ),
        "work_root": _disk_payload(
            settings.work_root,
            label="work_root",
        ),
        "archive_host_root": _remote_root_payload(
            host=getattr(
                settings,
                "archive_host",
                None,
            ),
            user=getattr(
                settings,
                "archive_user",
                None,
            ),
        ),
    }

    now = datetime.now(
        timezone.utc
    ).isoformat(
        timespec="seconds"
    )

    return {
        "generated_at": now,
        "library": photos,
        "analysis": {
            "rolling_10m": rolling,
            "current_session": session,
            "estimated_minutes_remaining": eta_minutes,
            "estimated_hours_remaining": round(
                eta_minutes / 60.0,
                2,
            ) if eta_minutes is not None else None,
        },
        "ingest": ingest,
        "uploads": uploads,
        "posts": posts,
        "storage": storage,
    }
