# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from curator.analyze.provider import get_provider
from curator.config import load_settings
from curator.db import connect
from curator.storage.lifecycle import visual_path


GROUPING_VERSION = 1
PROMPT_VERSION = 1


def load_photos(
    photo_ids: list[int],
) -> list[dict]:
    placeholders = ",".join(
        "?" for _ in photo_ids
    )

    with connect() as db:
        rows = db.execute(
            f"""
            SELECT
                id,
                filename,
                work_path,
                captured_at,
                analysis_json,
                scores_json
            FROM photos
            WHERE id IN ({placeholders})
            ORDER BY captured_at, id
            """,
            photo_ids,
        ).fetchall()

    return [dict(row) for row in rows]


def make_contact_sheet(
    photos: list[dict],
) -> Path:
    settings = load_settings()

    out_dir = (
        settings.cache
        / "operation-grouping"
    )

    out_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    columns = 3
    cell_w = 640
    cell_h = 540

    rows = math.ceil(
        len(photos) / columns
    )

    canvas = Image.new(
        "RGB",
        (
            columns * cell_w,
            rows * cell_h,
        ),
        "white",
    )

    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()

    for index, photo in enumerate(photos):
        column = index % columns
        row = index // columns

        x = column * cell_w
        y = row * cell_h

        source = Image.open(
            visual_path(
                photo["id"]
            )
        ).convert("RGB")

        source.thumbnail(
            (600, 455)
        )

        image_x = (
            x
            + (cell_w - source.width) // 2
        )

        image_y = y + 15

        canvas.paste(
            source,
            (image_x, image_y),
        )

        label_y = y + 480

        draw.rectangle(
            (
                x + 5,
                label_y - 5,
                x + cell_w - 5,
                y + cell_h - 5,
            ),
            fill="white",
        )

        draw.text(
            (x + 18, label_y),
            (
                f"PHOTO {photo['id']} | "
                f"{photo['filename']}"
            ),
            fill="black",
            font=font,
        )

        draw.text(
            (x + 18, label_y + 20),
            str(
                photo["captured_at"]
                or "unknown time"
            ),
            fill="black",
            font=font,
        )

    ids = "-".join(
        str(p["id"])
        for p in photos
    )

    output = (
        out_dir
        / f"operations-{ids}.jpg"
    )

    canvas.save(
        output,
        quality=92,
    )

    return output


def build_context(
    photos: list[dict],
) -> str:
    blocks = []

    for photo in photos:
        analysis = json.loads(
            photo["analysis_json"]
        )

        scores = json.loads(
            photo["scores_json"]
        )

        blocks.append(
            f"""
PHOTO {photo["id"]}
Filename: {photo["filename"]}
Captured: {photo["captured_at"]}
Description: {analysis.get("description")}
Shot type: {analysis.get("content", {}).get("shot_type")}
Primary subject: {analysis.get("content", {}).get("primary_subject")}
Scores: {json.dumps(scores)}
""".strip()
        )

    return "\n\n".join(blocks)


def build_prompt(
    photos: list[dict],
) -> str:
    context = build_context(
        photos
    )

    return f"""
You are the operation editor for a personal
Instagram photo-curation system.

You are looking at a labeled contact sheet.
Each image is labeled PHOTO <id>.

An "operation" means a short photographic
attempt at essentially one visual idea.

Examples:
- several group photos taken seconds apart
- multiple poses in front of the same landmark
- several attempts at the same composition
- one mini-sequence where expressions change

Do NOT group photos merely because they occurred
on the same day or at the same location.

Your job has two stages:

1. Group the photos into photographic operations.

2. Within every operation, decide whether the
operation deserves:

single
    One definitive image is enough.

multiple
    More than one image is genuinely distinct
    enough to deserve possible use together.

sequence
    The change between images is itself valuable.

reserve
    An alternate has a specific plausible future
    editorial purpose. Use this very sparingly.

abandon
    Nothing from the operation is worth carrying
    forward.

For every photo, assign one disposition:

primary
alternate
sequence
reserve
abandon

"alternate" does NOT mean it automatically
deserves posting. It means it is meaningfully
different enough to remain available during
post composition.

Be aggressive about redundancy.
Do not preserve merely decent alternates.

IMPORTANT:
A visually weaker candid may still matter if it
provides humor, emotion, or a distinct moment.

Do not make final Instagram posts yet.
This stage is only organizing the raw photographs.

Return ONLY valid JSON with this exact shape:

{{
  "operations": [
    {{
      "label": "...",
      "description": "...",
      "decision": "single|multiple|sequence|reserve|abandon",
      "reasoning": "...",
      "photos": [
        {{
          "photo_id": 1,
          "disposition": "primary|alternate|sequence|reserve|abandon",
          "rank": 1,
          "reason": "..."
        }}
      ]
    }}
  ]
}}

Every supplied photo ID must appear exactly once.

Here is the existing individual analysis:

{context}
""".strip()


def parse_json(text: str) -> dict:
    cleaned = text.strip()

    if cleaned.startswith("```"):
        lines = cleaned.splitlines()

        if lines[0].startswith("```"):
            lines = lines[1:]

        if lines[-1].startswith("```"):
            lines = lines[:-1]

        cleaned = "\n".join(
            lines
        ).strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")

    if start >= 0 and end > start:
        cleaned = cleaned[
            start:end + 1
        ]

    return json.loads(cleaned)


def validate_result(
    result: dict,
    expected_ids: set[int],
) -> None:
    seen = []

    valid_operation_decisions = {
        "single",
        "multiple",
        "sequence",
        "reserve",
        "abandon",
    }

    valid_dispositions = {
        "primary",
        "alternate",
        "sequence",
        "reserve",
        "abandon",
    }

    for operation in result["operations"]:
        if (
            operation["decision"]
            not in valid_operation_decisions
        ):
            raise ValueError(
                "Invalid operation decision"
            )

        for photo in operation["photos"]:
            photo_id = int(
                photo["photo_id"]
            )

            seen.append(photo_id)

            if (
                photo["disposition"]
                not in valid_dispositions
            ):
                raise ValueError(
                    "Invalid photo disposition"
                )

    if len(seen) != len(set(seen)):
        raise ValueError(
            "Photo appeared in multiple operations"
        )

    if set(seen) != expected_ids:
        raise ValueError(
            f"Photo ID mismatch. "
            f"Expected {expected_ids}, "
            f"got {set(seen)}"
        )


def save_result(
    result: dict,
    photo_ids: list[int],
    model: str,
) -> None:
    with connect() as db:
        placeholders = ",".join(
            "?" for _ in photo_ids
        )

        old_operation_ids = [
            row["operation_id"]
            for row in db.execute(
                f"""
                SELECT DISTINCT operation_id
                FROM operation_photos
                WHERE photo_id IN ({placeholders})
                """,
                photo_ids,
            ).fetchall()
        ]

        db.execute(
            f"""
            DELETE FROM operation_photos
            WHERE photo_id IN ({placeholders})
            """,
            photo_ids,
        )

        for operation_id in old_operation_ids:
            remaining = db.execute(
                """
                SELECT COUNT(*) AS count
                FROM operation_photos
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()["count"]

            if remaining == 0:
                db.execute(
                    """
                    DELETE FROM operations
                    WHERE id = ?
                    """,
                    (operation_id,),
                )

        for operation in result["operations"]:
            description = (
                f"[{operation['decision']}] "
                f"{operation['description']} "
                f"Reasoning: "
                f"{operation['reasoning']}"
            )

            cursor = db.execute(
                """
                INSERT INTO operations (
                    label,
                    description,
                    grouping_version
                )
                VALUES (?, ?, ?)
                """,
                (
                    operation["label"],
                    description,
                    GROUPING_VERSION,
                ),
            )

            operation_id = (
                cursor.lastrowid
            )

            for photo in operation["photos"]:
                db.execute(
                    """
                    INSERT INTO operation_photos (
                        operation_id,
                        photo_id,
                        decision,
                        decision_reason,
                        operation_rank
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        operation_id,
                        int(photo["photo_id"]),
                        photo["disposition"],
                        photo["reason"],
                        int(photo["rank"]),
                    ),
                )

        db.execute(
            """
            INSERT INTO ai_runs (
                stage,
                model,
                prompt_version,
                pipeline_version,
                input_fingerprint,
                output_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "operation_grouping",
                model,
                PROMPT_VERSION,
                GROUPING_VERSION,
                "photos:"
                + ",".join(
                    map(str, photo_ids)
                ),
                json.dumps(
                    result,
                    ensure_ascii=False,
                ),
            ),
        )

        db.commit()


def group_operations(
    photo_ids: list[int],
) -> dict:
    photos = load_photos(
        photo_ids
    )

    expected_ids = set(
        photo_ids
    )

    found_ids = {
        photo["id"]
        for photo in photos
    }

    if found_ids != expected_ids:
        raise ValueError(
            f"Missing photos: "
            f"{expected_ids - found_ids}"
        )

    for photo in photos:
        if (
            not photo["analysis_json"]
            or not photo["scores_json"]
        ):
            raise ValueError(
                f"Photo {photo['id']} "
                "has not been analyzed"
            )

    sheet = make_contact_sheet(
        photos
    )

    provider = get_provider(
        "CURATOR_CURATION_MODEL"
    )

    response = provider.analyze_image(
        sheet,
        build_prompt(photos),
        detail="high",
    )

    result = parse_json(
        response
    )

    validate_result(
        result,
        expected_ids,
    )

    save_result(
        result,
        photo_ids,
        provider.model,
    )

    return {
        "contact_sheet": str(sheet),
        "model": provider.model,
        "result": result,
    }
