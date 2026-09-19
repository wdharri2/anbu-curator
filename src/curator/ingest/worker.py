# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import signal
import time
from pathlib import Path

from curator.db import connect
from curator.ingest.manual import ingest_file
from curator.storage.lifecycle import ensure_proxy
from curator.storage.replicas import (
    MIN_DURABLE_ORIGINAL_REPLICAS,
    durable_original_count,
    inventory_photo,
)



running = True


def stop_worker(signum, frame):
    global running
    running = False


def recover_interrupted_jobs() -> None:
    with connect() as db:
        db.execute(
            """
            UPDATE ingest_jobs
            SET status = 'pending',
                result_message = 'Recovered after worker restart',
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'processing'
            """
        )
        db.commit()


def claim_next_job():
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")

        row = db.execute(
            """
            SELECT *
            FROM ingest_jobs
            WHERE status = 'pending'
            ORDER BY id
            LIMIT 1
            """
        ).fetchone()

        if row is None:
            db.commit()
            return None

        db.execute(
            """
            UPDATE ingest_jobs
            SET status = 'processing',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (row["id"],),
        )

        db.commit()
        return dict(row)


def finish_job(job_id: int, photo_id: int, message: str) -> None:
    with connect() as db:
        db.execute(
            """
            UPDATE ingest_jobs
            SET status = 'done',
                photo_id = ?,
                result_message = ?,
                error_message = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (photo_id, message, job_id),
        )
        db.commit()


def fail_job(job_id: int, error: str) -> None:
    with connect() as db:
        db.execute(
            """
            UPDATE ingest_jobs
            SET status = 'error',
                error_message = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (error[:4000], job_id),
        )
        db.commit()


def process_job(job: dict) -> None:
    path = Path(job["staging_path"])

    print(
        f'job={job["id"]} '
        f'file={job["original_filename"]!r} '
        f'status=processing',
        flush=True,
    )

    try:
        result = ingest_file(
            path,
            original_filename=job["original_filename"],
            source=job["source"],
        )

        photo_id = result["photo_id"]

        print(
            f'job={job["id"]} '
            f'photo={photo_id} '
            f'status=verifying_storage',
            flush=True,
        )

        # Build the local proxy while the full local
        # original is definitely available.
        proxy = ensure_proxy(
            photo_id
        )

        # Inventory verifies both the Local working
        # original and the Archive archive against the
        # database SHA-256.
        inventory_photo(
            photo_id,
            verify_remote=True,
        )

        durable_count = durable_original_count(
            photo_id
        )

        if (
            durable_count
            < MIN_DURABLE_ORIGINAL_REPLICAS
        ):
            raise RuntimeError(
                "Photo is not durably protected: "
                f"{durable_count}/"
                f"{MIN_DURABLE_ORIGINAL_REPLICAS} "
                "verified durable originals"
            )

        if not proxy.exists():
            raise RuntimeError(
                "Visual proxy was not created"
            )

        message = (
            f"{result['status']}; "
            f"durable_replicas={durable_count}; "
            "proxy=ready"
        )

        finish_job(
            job["id"],
            photo_id,
            message,
        )

        # Staging is disposable only after the
        # permanent ingest and storage verification
        # have both succeeded.
        if path.exists():
            path.unlink()

        print(
            f'job={job["id"]} '
            f'photo={photo_id} '
            f'status=ready '
            f'durable_replicas={durable_count} '
            f'proxy=ready',
            flush=True,
        )

    except Exception as exc:
        fail_job(job["id"], repr(exc))

        print(
            f'job={job["id"]} '
            f'status=error '
            f'error={exc!r}',
            flush=True,
        )


def main() -> None:
    signal.signal(signal.SIGTERM, stop_worker)
    signal.signal(signal.SIGINT, stop_worker)

    recover_interrupted_jobs()

    print("INGEST_WORKER_READY", flush=True)

    while running:
        job = claim_next_job()

        if job is None:
            time.sleep(1)
            continue

        process_job(job)

    print("INGEST_WORKER_STOPPED", flush=True)


if __name__ == "__main__":
    main()
