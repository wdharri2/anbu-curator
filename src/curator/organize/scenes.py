# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont, ImageOps

from curator.analyze.provider import get_provider
from curator.db import connect
from curator.storage.lifecycle import visual_path


GROUPING_VERSION = 1
PROMPT_VERSION = 1

SCOPES = {
    "specific_event",
    "outing",
    "day",
    "multi_day",
    "ambient",
    "other",
}


def parse_time(value: str | None) -> datetime | None:
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
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def haversine_km(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    radius = 6371.0088

    p1 = math.radians(lat1)
    p2 = math.radians(lat2)

    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)

    a = (
        math.sin(dp / 2) ** 2
        + math.cos(p1)
        * math.cos(p2)
        * math.sin(dl / 2) ** 2
    )

    return (
        2
        * radius
        * math.asin(
            math.sqrt(a)
        )
    )


def filename_number(
    filename: str,
) -> int | None:
    matches = re.findall(
        r"\d+",
        filename,
    )

    if not matches:
        return None

    try:
        return int(matches[-1])
    except ValueError:
        return None


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
                p.id,
                p.filename,
                p.source,
                p.work_path,
                p.captured_at,
                p.latitude,
                p.longitude,
                p.width,
                p.height,
                p.analysis_json,
                p.scores_json,

                (
                    SELECT o.label
                    FROM operation_photos op
                    JOIN operations o
                      ON o.id = op.operation_id
                    WHERE op.photo_id = p.id
                    LIMIT 1
                ) AS operation_label,

                (
                    SELECT op.decision
                    FROM operation_photos op
                    WHERE op.photo_id = p.id
                    LIMIT 1
                ) AS operation_disposition

            FROM photos p

            WHERE p.id IN ({placeholders})

            ORDER BY
                p.captured_at,
                p.id
            """,
            photo_ids,
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def relationship(
    a: dict,
    b: dict,
) -> dict:
    ta = parse_time(
        a["captured_at"]
    )

    tb = parse_time(
        b["captured_at"]
    )

    minutes = None
    same_day = None

    if ta and tb:
        minutes = abs(
            (tb - ta).total_seconds()
        ) / 60

        same_day = (
            ta.date()
            == tb.date()
        )

    distance = None

    if (
        a["latitude"] is not None
        and a["longitude"] is not None
        and b["latitude"] is not None
        and b["longitude"] is not None
    ):
        distance = haversine_km(
            a["latitude"],
            a["longitude"],
            b["latitude"],
            b["longitude"],
        )

    na = filename_number(
        a["filename"]
    )

    nb = filename_number(
        b["filename"]
    )

    filename_gap = None

    if na is not None and nb is not None:
        filename_gap = abs(
            nb - na
        )

    clues = []

    if minutes is not None:
        if minutes <= 2:
            clues.append(
                "very_close_in_time"
            )
        elif minutes <= 15:
            clues.append(
                "close_in_time"
            )
        elif minutes <= 90:
            clues.append(
                "same_general_period"
            )
        elif same_day:
            clues.append(
                "same_day"
            )
        elif minutes <= 60 * 24 * 7:
            clues.append(
                "same_week"
            )
        elif minutes <= 60 * 24 * 31:
            clues.append(
                "same_trip_scale"
            )

    if distance is not None:
        if distance <= 0.05:
            clues.append(
                "essentially_same_location"
            )
        elif distance <= 0.5:
            clues.append(
                "nearby_location"
            )
        elif distance <= 5:
            clues.append(
                "same_local_area"
            )

    if (
        filename_gap is not None
        and filename_gap <= 3
    ):
        clues.append(
            "nearby_filename_sequence"
        )

    if a["source"] == b["source"]:
        clues.append(
            "same_source"
        )

    return {
        "photo_a": a["id"],
        "photo_b": b["id"],
        "minutes_apart": (
            round(minutes, 2)
            if minutes is not None
            else None
        ),
        "distance_km": (
            round(distance, 3)
            if distance is not None
            else None
        ),
        "filename_gap": filename_gap,
        "same_day": same_day,
        "clues": clues,
    }


def build_relationships(
    photos: list[dict],
) -> list[dict]:
    relationships = []

    for i, a in enumerate(photos):
        for b in photos[i + 1:]:
            rel = relationship(
                a,
                b,
            )

            # Don't flood the model with useless
            # distant relationships.
            if rel["clues"]:
                relationships.append(
                    rel
                )

    return relationships


def make_contact_sheet(
    photos: list[dict],
) -> Path:
    load_dotenv(
        ".env",
        override=True,
    )

    root = Path(
        os.environ[
            "CURATOR_CACHE"
        ]
    ) / "scene-discovery"

    root.mkdir(
        parents=True,
        exist_ok=True,
    )

    columns = 3
    cell_w = 620
    cell_h = 535

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

    draw = ImageDraw.Draw(
        canvas
    )

    font = ImageFont.load_default()

    for index, photo in enumerate(
        photos
    ):
        column = index % columns
        row = index // columns

        x = column * cell_w
        y = row * cell_h

        image = Image.open(
            visual_path(
                photo["id"]
            )
        )

        # Respect iPhone / camera EXIF orientation.
        image = ImageOps.exif_transpose(
            image
        ).convert("RGB")

        image.thumbnail(
            (580, 445)
        )

        image_x = (
            x
            + (cell_w - image.width) // 2
        )

        image_y = y + 10

        canvas.paste(
            image,
            (
                image_x,
                image_y,
            ),
        )

        label_y = y + 465

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
            (
                x + 15,
                label_y,
            ),
            (
                f"PHOTO {photo['id']} | "
                f"{photo['filename']}"
            ),
            fill="black",
            font=font,
        )

        draw.text(
            (
                x + 15,
                label_y + 20,
            ),
            str(
                photo["captured_at"]
                or "unknown time"
            ),
            fill="black",
            font=font,
        )

        if photo["latitude"] is not None:
            draw.text(
                (
                    x + 15,
                    label_y + 40,
                ),
                (
                    f"{photo['latitude']:.5f}, "
                    f"{photo['longitude']:.5f}"
                ),
                fill="black",
                font=font,
            )

    ids = "-".join(
        str(p["id"])
        for p in photos
    )

    output = (
        root
        / f"scene-{ids}.jpg"
    )

    canvas.save(
        output,
        "JPEG",
        quality=90,
    )

    return output


def analysis_context(
    photos: list[dict],
) -> list[dict]:
    result = []

    for photo in photos:
        analysis = {}

        if photo["analysis_json"]:
            analysis = json.loads(
                photo["analysis_json"]
            )

        result.append(
            {
                "photo_id": photo["id"],
                "filename": photo["filename"],
                "source": photo["source"],
                "captured_at": photo[
                    "captured_at"
                ],
                "latitude": photo[
                    "latitude"
                ],
                "longitude": photo[
                    "longitude"
                ],
                "operation_label": photo[
                    "operation_label"
                ],
                "operation_disposition": photo[
                    "operation_disposition"
                ],
                "visual_description": analysis.get(
                    "description"
                ),
            }
        )

    return result


def build_prompt(
    photos: list[dict],
    relationships: list[dict],
) -> str:
    photo_context = json.dumps(
        analysis_context(photos),
        ensure_ascii=False,
        indent=2,
    )

    relationship_context = json.dumps(
        relationships,
        ensure_ascii=False,
        indent=2,
    )

    return f"""
You are the CONTEXT ORGANIZER for a personal
photo-curation system.

You are NOT making Instagram posts yet.

Your job is to discover useful SCENES and EVENTS
among these photographs.

Definitions:

SCENE:
A relatively specific visual or situational context.

Examples:
- standing around one festival area
- riding a train
- dinner at one restaurant
- walking a neighborhood
- several minutes at one landmark

EVENT:
A broader real-world context containing one or more
scenes.

Examples:
- one festival visit
- an evening out
- a sightseeing outing
- a travel day
- a full day when that is genuinely coherent

IMPORTANT:

METADATA IS SOFT EVIDENCE, NOT A RULE.

Use capture time, GPS, filename sequence, device/source,
and existing operation information to help understand
relationships.

Rough intuition:

- seconds/minutes apart is strong evidence
- same place and close time is strong evidence
- same hour is useful evidence
- same day is moderate evidence
- same week or same month/trip is weak background context

But NEVER group photographs merely because their dates
are close.

Likewise, NEVER separate photographs merely because
their dates differ.

Visual subject, activity, location, continuity, and
real-world meaning can outweigh time.

Two photos taken one minute apart may be unrelated.

Two scenes several hours apart may belong to the same
outing.

A later POST DISCOVERY stage is allowed to combine
different events, different days, or even completely
non-chronological material for aesthetic, thematic,
personal, or message-based reasons.

Therefore scenes/events are CONTEXT, not post boundaries.

Operation grouping from the prior stage is also only
evidence. Do not blindly copy it.

Every supplied photo must belong to exactly one scene.

Every scene must belong to exactly one event in THIS
organizational pass.

An event is allowed to contain only one scene.

Allowed event scopes:

specific_event
outing
day
multi_day
ambient
other

Return ONLY valid JSON:

{{
  "scenes": [
    {{
      "scene_key": "S1",
      "label": "...",
      "description": "...",
      "photo_ids": [1, 2],
      "reasoning": "...",
      "confidence": 0.90
    }}
  ],

  "events": [
    {{
      "event_key": "E1",
      "label": "...",
      "description": "...",
      "scope": "outing",
      "scene_keys": ["S1", "S2"],
      "reasoning": "...",
      "confidence": 0.85
    }}
  ]
}}

Confidence is 0.0 through 1.0.

PHOTO CONTEXT:

{photo_context}

PAIRWISE METADATA CLUES:

{relationship_context}
""".strip()


def parse_json(
    text: str,
) -> dict:
    cleaned = text.strip()

    if cleaned.startswith("```"):
        lines = cleaned.splitlines()

        if lines[0].startswith("```"):
            lines = lines[1:]

        if (
            lines
            and lines[-1].startswith("```")
        ):
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

    return json.loads(
        cleaned
    )


def validate(
    result: dict,
    expected_ids: set[int],
) -> None:
    scenes = result["scenes"]
    events = result["events"]

    scene_keys = [
        x["scene_key"]
        for x in scenes
    ]

    if (
        len(scene_keys)
        != len(set(scene_keys))
    ):
        raise ValueError(
            "Duplicate scene keys"
        )

    photo_ids = []

    for scene in scenes:
        photo_ids.extend(
            int(x)
            for x in scene["photo_ids"]
        )

        confidence = float(
            scene["confidence"]
        )

        if not 0 <= confidence <= 1:
            raise ValueError(
                "Invalid scene confidence"
            )

    if (
        len(photo_ids)
        != len(set(photo_ids))
    ):
        raise ValueError(
            "A photo appears in multiple scenes"
        )

    if set(photo_ids) != expected_ids:
        raise ValueError(
            "Scene photo membership mismatch: "
            f"expected={sorted(expected_ids)} "
            f"got={sorted(set(photo_ids))}"
        )

    event_scene_keys = []

    for event in events:
        if event["scope"] not in SCOPES:
            raise ValueError(
                "Invalid event scope: "
                f"{event['scope']}"
            )

        event_scene_keys.extend(
            event["scene_keys"]
        )

        confidence = float(
            event["confidence"]
        )

        if not 0 <= confidence <= 1:
            raise ValueError(
                "Invalid event confidence"
            )

    if (
        len(event_scene_keys)
        != len(set(event_scene_keys))
    ):
        raise ValueError(
            "A scene appears in multiple events"
        )

    if set(event_scene_keys) != set(
        scene_keys
    ):
        raise ValueError(
            "Event scene membership mismatch"
        )


def member_bounds(
    photo_ids: list[int],
) -> tuple:
    placeholders = ",".join(
        "?" for _ in photo_ids
    )

    with connect() as db:
        rows = db.execute(
            f"""
            SELECT
                captured_at,
                latitude,
                longitude
            FROM photos
            WHERE id IN ({placeholders})
            """,
            photo_ids,
        ).fetchall()

    times = [
        row["captured_at"]
        for row in rows
        if row["captured_at"]
    ]

    points = [
        (
            row["latitude"],
            row["longitude"],
        )
        for row in rows
        if (
            row["latitude"] is not None
            and row["longitude"] is not None
        )
    ]

    started = (
        min(times)
        if times
        else None
    )

    ended = (
        max(times)
        if times
        else None
    )

    if points:
        latitude = sum(
            p[0]
            for p in points
        ) / len(points)

        longitude = sum(
            p[1]
            for p in points
        ) / len(points)
    else:
        latitude = None
        longitude = None

    return (
        started,
        ended,
        latitude,
        longitude,
    )


def save_result(
    result: dict,
    photo_ids: list[int],
    *,
    model: str,
) -> None:
    with connect() as db:
        placeholders = ",".join(
            "?" for _ in photo_ids
        )

        # Remove previous scene memberships for this
        # exact test material.
        old_scene_ids = [
            row["scene_id"]
            for row in db.execute(
                f"""
                SELECT DISTINCT scene_id
                FROM scene_photos
                WHERE photo_id IN ({placeholders})
                """,
                photo_ids,
            ).fetchall()
        ]

        db.execute(
            f"""
            DELETE FROM scene_photos
            WHERE photo_id IN ({placeholders})
            """,
            photo_ids,
        )

        for scene_id in old_scene_ids:
            remaining = db.execute(
                """
                SELECT COUNT(*) AS c
                FROM scene_photos
                WHERE scene_id = ?
                """,
                (scene_id,),
            ).fetchone()["c"]

            if remaining == 0:
                event_ids = [
                    row["event_id"]
                    for row in db.execute(
                        """
                        SELECT event_id
                        FROM event_scenes
                        WHERE scene_id = ?
                        """,
                        (scene_id,),
                    ).fetchall()
                ]

                db.execute(
                    """
                    DELETE FROM event_scenes
                    WHERE scene_id = ?
                    """,
                    (scene_id,),
                )

                db.execute(
                    """
                    DELETE FROM scenes
                    WHERE id = ?
                    """,
                    (scene_id,),
                )

                for event_id in event_ids:
                    remaining_events = db.execute(
                        """
                        SELECT COUNT(*) AS c
                        FROM event_scenes
                        WHERE event_id = ?
                        """,
                        (event_id,),
                    ).fetchone()["c"]

                    if remaining_events == 0:
                        db.execute(
                            """
                            DELETE FROM events
                            WHERE id = ?
                            """,
                            (event_id,),
                        )

        scene_map = {}

        for scene in result["scenes"]:
            members = [
                int(x)
                for x in scene["photo_ids"]
            ]

            (
                started,
                ended,
                latitude,
                longitude,
            ) = member_bounds(
                members
            )

            cursor = db.execute(
                """
                INSERT INTO scenes (
                    label,
                    description,
                    started_at,
                    ended_at,
                    latitude,
                    longitude,
                    grouping_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scene["label"],
                    scene["description"],
                    started,
                    ended,
                    latitude,
                    longitude,
                    GROUPING_VERSION,
                ),
            )

            scene_id = cursor.lastrowid

            scene_map[
                scene["scene_key"]
            ] = scene_id

            for rank, photo_id in enumerate(
                members,
                start=1,
            ):
                db.execute(
                    """
                    INSERT INTO scene_photos (
                        scene_id,
                        photo_id,
                        scene_rank,
                        confidence,
                        reason
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        scene_id,
                        photo_id,
                        rank,
                        float(
                            scene[
                                "confidence"
                            ]
                        ),
                        scene["reasoning"],
                    ),
                )

        for event in result["events"]:
            scene_ids = [
                scene_map[key]
                for key in event[
                    "scene_keys"
                ]
            ]

            placeholders2 = ",".join(
                "?"
                for _ in scene_ids
            )

            rows = db.execute(
                f"""
                SELECT
                    started_at,
                    ended_at
                FROM scenes
                WHERE id IN ({placeholders2})
                """,
                scene_ids,
            ).fetchall()

            starts = [
                row["started_at"]
                for row in rows
                if row["started_at"]
            ]

            ends = [
                row["ended_at"]
                for row in rows
                if row["ended_at"]
            ]

            started = (
                min(starts)
                if starts
                else None
            )

            ended = (
                max(ends)
                if ends
                else None
            )

            cursor = db.execute(
                """
                INSERT INTO events (
                    label,
                    description,
                    scope,
                    started_at,
                    ended_at,
                    grouping_version
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event["label"],
                    event["description"],
                    event["scope"],
                    started,
                    ended,
                    GROUPING_VERSION,
                ),
            )

            event_id = cursor.lastrowid

            for rank, scene_key in enumerate(
                event["scene_keys"],
                start=1,
            ):
                db.execute(
                    """
                    INSERT INTO event_scenes (
                        event_id,
                        scene_id,
                        event_rank,
                        confidence,
                        reason
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        scene_map[
                            scene_key
                        ],
                        rank,
                        float(
                            event[
                                "confidence"
                            ]
                        ),
                        event["reasoning"],
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
                "scene_event_discovery",
                model,
                PROMPT_VERSION,
                GROUPING_VERSION,
                (
                    "photos:"
                    + ",".join(
                        str(x)
                        for x in sorted(
                            photo_ids
                        )
                    )
                ),
                json.dumps(
                    result,
                    ensure_ascii=False,
                ),
            ),
        )

        db.commit()


def discover(
    photo_ids: list[int],
) -> dict:
    photos = load_photos(
        photo_ids
    )

    found = {
        p["id"]
        for p in photos
    }

    expected = set(
        photo_ids
    )

    if found != expected:
        raise ValueError(
            f"Missing photos: "
            f"{sorted(expected - found)}"
        )

    relationships = build_relationships(
        photos
    )

    sheet = make_contact_sheet(
        photos
    )

    provider = get_provider(
        "CURATOR_CURATION_MODEL"
    )

    response = provider.analyze_image(
        sheet,
        build_prompt(
            photos,
            relationships,
        ),
        detail="high",
    )

    result = parse_json(
        response
    )

    validate(
        result,
        expected,
    )

    save_result(
        result,
        photo_ids,
        model=provider.model,
    )

    return {
        "model": provider.model,
        "contact_sheet": str(sheet),
        "relationships": relationships,
        "result": result,
    }
