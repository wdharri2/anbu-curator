# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import argparse
import json
from pathlib import Path

from curator.db import connect
from curator.storage.lifecycle import (
    ensure_proxy,
    restore_original,
    safe_evict,
    sha256_file,
    verify_archive,
)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "photo_id",
        type=int,
    )

    args = parser.parse_args()

    photo_id = args.photo_id

    with connect() as db:
        photo = db.execute(
            """
            SELECT
                id,
                sha256,
                filename,
                work_path
            FROM photos
            WHERE id = ?
            """,
            (photo_id,),
        ).fetchone()

    if not photo:
        raise SystemExit(
            "Photo not found"
        )

    work = Path(
        photo["work_path"]
    )

    print(
        f"photo={photo_id} "
        f"{photo['filename']}"
    )

    print(
        "work_exists_before="
        f"{work.exists()}"
    )

    if not work.exists():
        print(
            "Original already absent; "
            "restoring before test."
        )

        restore_original(
            photo_id
        )

    before_hash = sha256_file(
        work
    )

    print(
        "local_hash_before="
        + before_hash
    )

    archive = verify_archive(
        photo_id
    )

    print(
        "archive_verified="
        + str(
            archive["verified"]
        )
    )

    proxy = ensure_proxy(
        photo_id
    )

    print(
        f"proxy={proxy}"
    )

    eviction = safe_evict(
        photo_id
    )

    print(
        json.dumps(
            eviction,
            indent=2,
        )
    )

    print(
        "work_exists_after_evict="
        f"{work.exists()}"
    )

    restoration = restore_original(
        photo_id
    )

    print(
        json.dumps(
            restoration,
            indent=2,
        )
    )

    after_hash = sha256_file(
        work
    )

    print(
        "local_hash_after="
        + after_hash
    )

    if before_hash != after_hash:
        raise RuntimeError(
            "ROUND TRIP HASH MISMATCH"
        )

    if after_hash != photo["sha256"]:
        raise RuntimeError(
            "RESTORED HASH DOES NOT MATCH DB"
        )

    print(
        "ROUND_TRIP_OK"
    )


if __name__ == "__main__":
    main()
