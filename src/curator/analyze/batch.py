# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import argparse
import traceback

from curator.analyze.photo import analyze_photo
from curator.db import connect


def get_photo_ids(
    *,
    source: str | None = None,
    force: bool = False,
    explicit_ids: list[int] | None = None,
) -> list[int]:

    if explicit_ids:
        placeholders = ",".join("?" for _ in explicit_ids)

        query = f"""
        SELECT id
        FROM photos
        WHERE id IN ({placeholders})
        ORDER BY id
        """

        with connect() as db:
            rows = db.execute(
                query,
                explicit_ids,
            ).fetchall()

        return [row["id"] for row in rows]

    conditions = []
    params = []

    if source:
        conditions.append("source = ?")
        params.append(source)

    if not force:
        conditions.append(
            "analysis_status != 'done'"
        )

    where = ""

    if conditions:
        where = "WHERE " + " AND ".join(
            conditions
        )

    with connect() as db:
        rows = db.execute(
            f"""
            SELECT id
            FROM photos
            {where}
            ORDER BY id
            """,
            params,
        ).fetchall()

    return [row["id"] for row in rows]


def mark_processing(photo_id: int) -> None:
    with connect() as db:
        db.execute(
            """
            UPDATE photos
            SET analysis_status = 'processing',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (photo_id,),
        )
        db.commit()


def mark_error(
    photo_id: int,
    error: str,
) -> None:
    with connect() as db:
        db.execute(
            """
            UPDATE photos
            SET analysis_status = 'error',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (photo_id,),
        )

        db.execute(
            """
            INSERT INTO ai_runs (
                stage,
                input_fingerprint,
                output_json
            )
            VALUES (
                'photo_analysis_error',
                ?,
                ?
            )
            """,
            (
                f"photo:{photo_id}",
                error[:8000],
            ),
        )

        db.commit()


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--source",
        default="web_upload",
        help="Only analyze photos from this source",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-analyze photos even if already done",
    )

    parser.add_argument(
        "--ids",
        nargs="+",
        type=int,
        help="Explicit photo IDs",
    )

    args = parser.parse_args()

    photo_ids = get_photo_ids(
        source=args.source,
        force=args.force,
        explicit_ids=args.ids,
    )

    print(
        f"photos_selected={len(photo_ids)}"
    )

    if not photo_ids:
        print("Nothing to analyze.")
        return

    done = 0
    failed = 0

    for index, photo_id in enumerate(
        photo_ids,
        start=1,
    ):
        print()
        print(
            f"[{index}/{len(photo_ids)}] "
            f"Analyzing photo {photo_id}"
        )

        try:
            mark_processing(photo_id)

            result = analyze_photo(photo_id)

            scores = result["parsed"]["scores"]

            print(
                "DONE"
                f" aura={scores['aura']}"
                f" photography={scores['photography_quality']}"
                f" story={scores['story_value']}"
                f" cover={scores['cover_potential']}"
            )

            done += 1

        except Exception as exc:
            failed += 1

            error = (
                f"{type(exc).__name__}: {exc}\n"
                + traceback.format_exc()
            )

            mark_error(
                photo_id,
                error,
            )

            print(
                f"ERROR photo={photo_id}: "
                f"{type(exc).__name__}: {exc}"
            )

    print()
    print(
        f"batch_done={done} "
        f"batch_failed={failed}"
    )


if __name__ == "__main__":
    main()
