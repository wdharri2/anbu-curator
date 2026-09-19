# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta

from curator.analyze.batch import (
    mark_error,
    mark_processing,
)
from curator.analyze.photo import analyze_photo
from curator.curate.discovery import (
    discover_post_groups,
    discovery_already_run,
)
from curator.curate.arbitration import (
    arbitrate_proposals,
)
from curator.curate.posts import curate_post
from curator.db import connect
from curator.organize.operations import (
    group_operations,
)
from curator.organize.scenes import discover


PIPELINE_VERSION = 1

# Workload controls only.
# These values are NOT editorial boundaries.
PROCESSING_GAP_HOURS = 18
MAX_ORGANIZE_BATCH = 24

# Cross-context discovery is intentionally broader.
DEFAULT_CONTEXT_DAYS = 31
MAX_DISCOVERY_PHOTOS = 100
DISCOVERY_OVERLAP = 20


def parse_capture_time(
    value: str | None,
) -> datetime | None:
    if not value:
        return None

    formats = (
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    )

    for fmt in formats:
        try:
            return datetime.strptime(
                value[:19],
                fmt,
            )
        except ValueError:
            pass

    try:
        return datetime.fromisoformat(
            value
        )
    except ValueError:
        return None


def parse_cli_date(
    value: str | None,
) -> datetime | None:
    if not value:
        return None

    return datetime.strptime(
        value,
        "%Y-%m-%d",
    )


def in_scope(
    photo: dict,
    start: datetime | None,
    end: datetime | None,
) -> bool:
    captured = parse_capture_time(
        photo["captured_at"]
    )

    if captured is None:
        return (
            start is None
            and end is None
        )

    if (
        start is not None
        and captured < start
    ):
        return False

    if end is not None:
        inclusive_end = (
            end
            + timedelta(days=1)
        )

        if captured >= inclusive_end:
            return False

    return True


def load_photos(
    source: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict]:
    with connect() as db:
        rows = db.execute(
            """
            SELECT
                id,
                filename,
                source,
                captured_at,
                latitude,
                longitude,
                analysis_status,
                curation_state,

                EXISTS (
                    SELECT 1
                    FROM operation_photos op
                    WHERE op.photo_id = photos.id
                ) AS has_operation,

                EXISTS (
                    SELECT 1
                    FROM scene_photos sp
                    WHERE sp.photo_id = photos.id
                ) AS has_scene

            FROM photos

            WHERE source = ?

            ORDER BY
                captured_at,
                id
            """,
            (source,),
        ).fetchall()

    photos = [
        dict(row)
        for row in rows
    ]

    return [
        photo
        for photo in photos
        if in_scope(
            photo,
            start,
            end,
        )
    ]


def analyze_pending(
    photos: list[dict],
) -> dict:
    pending = [
        photo
        for photo in photos
        if photo["analysis_status"]
        == "pending"
    ]

    completed = []
    errors = []

    for index, photo in enumerate(
        pending,
        start=1,
    ):
        photo_id = photo["id"]

        print(
            f"[analysis {index}/{len(pending)}] "
            f"{photo_id} {photo['filename']}"
        )

        try:
            mark_processing(
                photo_id
            )

            analyze_photo(
                photo_id
            )

            completed.append(
                photo_id
            )

            print(
                "  done"
            )

        except Exception as exc:
            message = (
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            mark_error(
                photo_id,
                message,
            )

            errors.append(
                {
                    "photo_id": photo_id,
                    "error": message,
                }
            )

            print(
                f"  ERROR {message}"
            )

    return {
        "completed": completed,
        "errors": errors,
    }


def organize_candidates(
    photos: list[dict],
) -> list[dict]:
    return [
        photo
        for photo in photos
        if (
            photo["analysis_status"]
            == "done"
            and (
                not photo["has_operation"]
                or not photo["has_scene"]
            )
        )
    ]


def make_organize_batches(
    photos: list[dict],
) -> list[list[dict]]:
    """
    Create manageable AI workloads.

    A batch is NOT:
    - a post,
    - an event,
    - a day,
    - or a hard semantic boundary.

    Later discovery can recombine material from
    different batches.
    """

    if not photos:
        return []

    ordered = sorted(
        photos,
        key=lambda p: (
            p["captured_at"] or "",
            p["id"],
        ),
    )

    batches = []
    current = []
    previous_time = None

    for photo in ordered:
        captured = parse_capture_time(
            photo["captured_at"]
        )

        split = False

        if (
            current
            and len(current)
            >= MAX_ORGANIZE_BATCH
        ):
            split = True

        if (
            current
            and previous_time
            and captured
        ):
            gap = (
                captured
                - previous_time
            ).total_seconds() / 3600

            if gap > PROCESSING_GAP_HOURS:
                split = True

        if split:
            batches.append(
                current
            )

            current = []

        current.append(
            photo
        )

        if captured:
            previous_time = captured

    if current:
        batches.append(
            current
        )

    return batches


def reload_scope(
    source: str,
    start: datetime | None,
    end: datetime | None,
) -> list[dict]:
    return load_photos(
        source,
        start=start,
        end=end,
    )


def discovery_context(
    photos: list[dict],
    anchor_ids: list[int],
    context_days: int,
    rediscover: bool,
) -> list[dict]:
    done = [
        p
        for p in photos
        if p["analysis_status"]
        == "done"
        and p["has_operation"]
        and p["has_scene"]
        and (
            p.get("curation_state")
            or "eligible"
        ) != "suppressed"
    ]

    if not done:
        return []

    if rediscover:
        return done

    anchor_id_set = set(
        anchor_ids
    )

    anchors = [
        p
        for p in done
        if (
            p["id"] in anchor_id_set
            or (
                p.get("curation_state")
                == "recurate"
            )
        )
    ]

    # No new organization work and no explicit
    # recuration request means normal broad discovery.
    if not anchors:
        return done

    anchor_photo_ids = {
        p["id"]
        for p in anchors
    }

    anchor_times = [
        parse_capture_time(
            p["captured_at"]
        )
        for p in anchors
    ]

    anchor_times = [
        t
        for t in anchor_times
        if t is not None
    ]

    # Preserve anchors even when their capture time
    # cannot be interpreted.
    if not anchor_times:
        return done

    lower = (
        min(anchor_times)
        - timedelta(
            days=context_days
        )
    )

    upper = (
        max(anchor_times)
        + timedelta(
            days=context_days
        )
    )

    context = []

    for photo in done:
        if photo["id"] in anchor_photo_ids:
            context.append(
                photo
            )
            continue

        captured = parse_capture_time(
            photo["captured_at"]
        )

        if captured is None:
            continue

        if lower <= captured <= upper:
            context.append(
                photo
            )

    return context

def discovery_windows(
    photos: list[dict],
) -> list[list[dict]]:
    """
    Windowing exists only to keep model context
    manageable.

    Windows overlap so editorial ideas near a
    workload boundary are not immediately lost.
    """

    if len(photos) <= MAX_DISCOVERY_PHOTOS:
        return [
            photos
        ] if photos else []

    ordered = sorted(
        photos,
        key=lambda p: (
            p["captured_at"] or "",
            p["id"],
        ),
    )

    step = (
        MAX_DISCOVERY_PHOTOS
        - DISCOVERY_OVERLAP
    )

    windows = []

    start = 0

    while start < len(ordered):
        window = ordered[
            start:
            start + MAX_DISCOVERY_PHOTOS
        ]

        if window:
            windows.append(
                window
            )

        if (
            start + MAX_DISCOVERY_PHOTOS
            >= len(ordered)
        ):
            break

        start += step

    return windows


def post_fingerprint(
    photo_ids: list[int],
) -> str:
    return (
        "photos:"
        + ",".join(
            str(x)
            for x in sorted(
                photo_ids
            )
        )
    )


def post_already_curated(
    photo_ids: list[int],
) -> bool:
    fp = post_fingerprint(
        photo_ids
    )

    with connect() as db:
        row = db.execute(
            """
            SELECT id
            FROM ai_runs
            WHERE stage IN (
                'post_curation',
                'post_curation_no_post'
            )
              AND input_fingerprint = ?
            LIMIT 1
            """,
            (fp,),
        ).fetchone()

    return row is not None


def consume_recuration_for_candidate(
    post_id: int,
) -> list[int]:
    with connect() as db:
        rows = db.execute(
            """
            SELECT p.id
            FROM photos p
            JOIN post_candidate_photos pcp
              ON pcp.photo_id = p.id
            WHERE pcp.post_id = ?
              AND p.curation_state = 'recurate'
            ORDER BY p.id
            """,
            (post_id,),
        ).fetchall()

        photo_ids = [
            row["id"]
            for row in rows
        ]

        if not photo_ids:
            return []

        placeholders = ",".join(
            "?" for _ in photo_ids
        )

        db.execute(
            f"""
            UPDATE photos
            SET
                curation_state = 'eligible',
                curation_state_updated_at =
                    CURRENT_TIMESTAMP,
                updated_at =
                    CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
              AND curation_state = 'recurate'
            """,
            photo_ids,
        )

        db.commit()

    return photo_ids

def supersede_candidates(
    candidate_ids: list[int],
    replacement_post_id: int,
) -> list[int]:
    ids = sorted(
        set(
            int(x)
            for x in candidate_ids
        )
    )

    if not ids:
        return []

    placeholders = ",".join(
        "?" for _ in ids
    )

    with connect() as db:
        rows = db.execute(
            f"""
            SELECT
                id,
                status
            FROM post_candidates
            WHERE id IN ({placeholders})
            """,
            ids,
        ).fetchall()

        found = {
            row["id"]: row["status"]
            for row in rows
        }

        missing = [
            candidate_id
            for candidate_id in ids
            if candidate_id not in found
        ]

        if missing:
            raise RuntimeError(
                "Replacement targets missing: "
                + ",".join(
                    map(str, missing)
                )
            )

        invalid = [
            candidate_id
            for candidate_id in ids
            if found[candidate_id]
            != "candidate"
        ]

        if invalid:
            raise RuntimeError(
                "Replacement targets are no "
                "longer active candidates: "
                + ",".join(
                    map(str, invalid)
                )
            )

        db.execute(
            f"""
            UPDATE post_candidates
            SET
                status = 'superseded',
                superseded_by_candidate_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
            """,
            [
                replacement_post_id,
                *ids,
            ],
        )

        db.commit()

    return ids


def record_run(
    result: dict,
) -> None:
    with connect() as db:
        db.execute(
            """
            INSERT INTO ai_runs (
                stage,
                pipeline_version,
                input_fingerprint,
                output_json
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                "library_pipeline",
                PIPELINE_VERSION,
                (
                    "source:"
                    + result["source"]
                ),
                json.dumps(
                    result,
                    ensure_ascii=False,
                ),
            ),
        )

        db.commit()


def run(
    *,
    source: str,
    start_date: str | None = None,
    end_date: str | None = None,
    context_days: int = DEFAULT_CONTEXT_DAYS,
    dry_run: bool = False,
    rediscover: bool = False,
    force_posts: bool = False,
) -> dict:
    start = parse_cli_date(
        start_date
    )

    end = parse_cli_date(
        end_date
    )

    print()
    print(
        "=== LIBRARY PROCESSING PIPELINE ==="
    )

    print(
        f"source={source}"
    )

    print(
        f"date_start={start_date or 'ANY'}"
    )

    print(
        f"date_end={end_date or 'ANY'}"
    )

    print(
        f"context_days={context_days}"
    )

    photos = load_photos(
        source,
        start=start,
        end=end,
    )

    print(
        f"photos_in_scope={len(photos)}"
    )

    pending_count = sum(
        1
        for p in photos
        if p["analysis_status"]
        == "pending"
    )

    print(
        f"pending_analysis={pending_count}"
    )

    if dry_run:
        analysis_result = {
            "completed": [],
            "errors": [],
        }
    else:
        analysis_result = analyze_pending(
            photos
        )

    # Analysis may have changed statuses.
    photos = reload_scope(
        source,
        start,
        end,
    )

    candidates = organize_candidates(
        photos
    )

    anchor_ids = [
        p["id"]
        for p in candidates
    ]

    batches = make_organize_batches(
        candidates
    )

    print(
        f"needs_organization="
        f"{len(candidates)}"
    )

    print(
        f"organization_batches="
        f"{len(batches)}"
    )

    organized = []

    for index, batch in enumerate(
        batches,
        start=1,
    ):
        ids = [
            p["id"]
            for p in batch
        ]

        print()
        print(
            f"[organize {index}/"
            f"{len(batches)}] "
            + ",".join(
                map(str, ids)
            )
        )

        if dry_run:
            organized.append(
                {
                    "photo_ids": ids,
                    "status": "dry_run",
                }
            )

            continue

        operation_result = (
            group_operations(
                ids
            )
        )

        scene_result = discover(
            ids
        )

        organized.append(
            {
                "photo_ids": ids,
                "operations": len(
                    operation_result[
                        "result"
                    ][
                        "operations"
                    ]
                ),
                "scenes": len(
                    scene_result[
                        "result"
                    ][
                        "scenes"
                    ]
                ),
                "events": len(
                    scene_result[
                        "result"
                    ][
                        "events"
                    ]
                ),
            }
        )

    # Reload after organization so new scene/operation
    # membership is visible.
    photos = reload_scope(
        source,
        start,
        end,
    )

    context = discovery_context(
        photos,
        anchor_ids,
        context_days,
        rediscover,
    )

    windows = discovery_windows(
        context
    )

    print()
    print(
        "=== POST DISCOVERY ==="
    )

    print(
        f"discovery_context_photos="
        f"{len(context)}"
    )

    print(
        f"discovery_windows="
        f"{len(windows)}"
    )

    proposals_seen = set()
    proposal_results = []
    post_results = []

    for index, window in enumerate(
        windows,
        start=1,
    ):
        ids = [
            p["id"]
            for p in window
        ]

        recurate_ids_in_window = {
            p["id"]
            for p in window
            if p.get("curation_state")
            == "recurate"
        }

        print()
        print(
            f"[discovery {index}/"
            f"{len(windows)}] "
            f"{len(ids)} photos"
        )

        if dry_run:
            proposal_results.append(
                {
                    "window": index,
                    "photo_ids": ids,
                    "status": "dry_run",
                }
            )

            continue

        if (
            not rediscover
            and not recurate_ids_in_window
            and discovery_already_run(
                ids
            )
        ):
            print(
                "  exact context already "
                "evaluated"
            )

            proposal_results.append(
                {
                    "window": index,
                    "status": (
                        "already_evaluated"
                    ),
                }
            )

            continue

        discovery = discover_post_groups(
            ids
        )

        proposals = discovery[
            "result"
        ][
            "proposals"
        ]

        print(
            f"  proposals={len(proposals)}"
        )

        arbitration = arbitrate_proposals(
            proposals
        )

        arb = arbitration[
            "arbitration"
        ]

        normalized = {
            int(
                proposal[
                    "proposal_index"
                ]
            ): proposal
            for proposal
            in arbitration[
                "proposals"
            ]
        }

        proposal_results.append(
            {
                "window": index,
                "proposals": proposals,
                "arbitration": arb,
            }
        )

        print(
            "  portfolio: "
            + arb[
                "portfolio_reasoning"
            ]
        )

        for decision in arb[
            "decisions"
        ]:
            proposal_index = int(
                decision[
                    "proposal_index"
                ]
            )

            proposal = normalized[
                proposal_index
            ]

            candidate_ids = sorted(
                int(x)
                for x in proposal[
                    "photo_ids"
                ]
            )

            portfolio_decision = (
                decision[
                    "decision"
                ]
            )

            print(
                "  [portfolio] "
                f"{portfolio_decision}: "
                f"{proposal['label']}"
            )

            if portfolio_decision not in {
                "select",
                "replace",
            }:
                post_results.append(
                    {
                        "label": proposal[
                            "label"
                        ],
                        "photo_ids": (
                            candidate_ids
                        ),
                        "status": (
                            "portfolio_"
                            + portfolio_decision
                        ),
                        "reason": decision[
                            "reason"
                        ],
                    }
                )

                continue

            replacement_candidate_ids = (
                sorted(
                    int(x)
                    for x in decision.get(
                        "replaces_candidate_ids",
                        [],
                    )
                )
                if portfolio_decision == "replace"
                else []
            )

            proposal_key = tuple(
                candidate_ids
            )

            proposal_has_recurate = bool(
                recurate_ids_in_window.intersection(
                    candidate_ids
                )
            )

            if proposal_key in proposals_seen:
                print(
                    "    duplicate selected "
                    "set from overlapping "
                    "discovery window"
                )

                post_results.append(
                    {
                        "label": proposal[
                            "label"
                        ],
                        "photo_ids": (
                            candidate_ids
                        ),
                        "status": (
                            "window_duplicate"
                        ),
                    }
                )

                continue

            proposals_seen.add(
                proposal_key
            )

            if (
                not force_posts
                and not proposal_has_recurate
                and post_already_curated(
                    candidate_ids
                )
            ):
                print(
                    "    already curated"
                )

                post_results.append(
                    {
                        "label": proposal[
                            "label"
                        ],
                        "photo_ids": (
                            candidate_ids
                        ),
                        "status": (
                            "already_curated"
                        ),
                    }
                )

                continue

            try:
                result = curate_post(
                    candidate_ids
                )

                if result["post_id"]:
                    if replacement_candidate_ids:
                        superseded = (
                            supersede_candidates(
                                replacement_candidate_ids,
                                result["post_id"],
                            )
                        )

                        print(
                            "    superseded candidates: "
                            + ",".join(
                                map(str, superseded)
                            )
                        )

                    consumed_recuration = (
                        consume_recuration_for_candidate(
                            result["post_id"]
                        )
                    )

                    if consumed_recuration:
                        print(
                            "    consumed recuration: "
                            + ",".join(
                                map(
                                    str,
                                    consumed_recuration,
                                )
                            )
                        )

                    status = (
                        "candidate_created"
                    )

                    print(
                        "    candidate "
                        f"{result['post_id']} "
                        "created"
                    )
                else:
                    status = "no_post"

                    print(
                        "    full curator "
                        "rejected proposal"
                    )

                post_results.append(
                    {
                        "label": proposal[
                            "label"
                        ],
                        "photo_ids": (
                            candidate_ids
                        ),
                        "status": status,
                        "post_id": result[
                            "post_id"
                        ],
                    }
                )

            except Exception as exc:
                message = (
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

                print(
                    f"    ERROR {message}"
                )

                post_results.append(
                    {
                        "label": proposal[
                            "label"
                        ],
                        "photo_ids": (
                            candidate_ids
                        ),
                        "status": "error",
                        "error": message,
                    }
                )

    result = {
        "pipeline_version": (
            PIPELINE_VERSION
        ),
        "source": source,
        "start_date": start_date,
        "end_date": end_date,
        "context_days": context_days,
        "dry_run": dry_run,
        "analysis": analysis_result,
        "organized": organized,
        "post_discovery": (
            proposal_results
        ),
        "posts": post_results,
    }

    if not dry_run:
        record_run(
            result
        )

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Incrementally process a photo "
            "library into curated post "
            "candidates."
        )
    )

    parser.add_argument(
        "--source",
        default="web_upload",
    )

    parser.add_argument(
        "--start-date",
        help="YYYY-MM-DD",
    )

    parser.add_argument(
        "--end-date",
        help="YYYY-MM-DD",
    )

    parser.add_argument(
        "--context-days",
        type=int,
        default=DEFAULT_CONTEXT_DAYS,
        help=(
            "Soft temporal neighborhood used "
            "for cross-context post discovery."
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
    )

    parser.add_argument(
        "--rediscover",
        action="store_true",
        help=(
            "Run post discovery even when "
            "the exact context has already "
            "been evaluated."
        ),
    )

    parser.add_argument(
        "--force-posts",
        action="store_true",
        help=(
            "Allow an already-curated exact "
            "photo set to be curated again."
        ),
    )

    args = parser.parse_args()

    result = run(
        source=args.source,
        start_date=args.start_date,
        end_date=args.end_date,
        context_days=args.context_days,
        dry_run=args.dry_run,
        rediscover=args.rediscover,
        force_posts=args.force_posts,
    )

    print()
    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
