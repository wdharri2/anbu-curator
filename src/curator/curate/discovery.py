# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import hashlib
import json
import os

from dotenv import load_dotenv
from openai import OpenAI

from curator.db import connect


DISCOVERY_VERSION = 1
PROMPT_VERSION = 1


def load_context(
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
                p.captured_at,
                p.latitude,
                p.longitude,
                p.analysis_json,
                p.scores_json,
                p.curation_state,

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
                ) AS operation_disposition,

                (
                    SELECT s.label
                    FROM scene_photos sp
                    JOIN scenes s
                      ON s.id = sp.scene_id
                    WHERE sp.photo_id = p.id
                    LIMIT 1
                ) AS scene_label,

                (
                    SELECT e.label
                    FROM scene_photos sp
                    JOIN event_scenes es
                      ON es.scene_id = sp.scene_id
                    JOIN events e
                      ON e.id = es.event_id
                    WHERE sp.photo_id = p.id
                    LIMIT 1
                ) AS event_label,

                (
                    SELECT e.scope
                    FROM scene_photos sp
                    JOIN event_scenes es
                      ON es.scene_id = sp.scene_id
                    JOIN events e
                      ON e.id = es.event_id
                    WHERE sp.photo_id = p.id
                    LIMIT 1
                ) AS event_scope,

                (
                    SELECT COUNT(*)
                    FROM published_post_photos ppp
                    WHERE ppp.photo_id = p.id
                ) AS times_published

            FROM photos p

            WHERE p.id IN ({placeholders})
              AND COALESCE(
                    p.curation_state,
                    'eligible'
                  ) != 'suppressed'

            ORDER BY
                CASE
                    WHEN p.curation_state = 'recurate'
                    THEN 0
                    ELSE 1
                END,
                p.captured_at,
                p.id
            """,
            photo_ids,
        ).fetchall()

        feedback_rows = db.execute(
            f"""
            SELECT
                pcp.photo_id,
                pc.id AS candidate_id,
                pc.title AS candidate_title,
                pc.status AS candidate_status,
                pc.editorial_feedback,
                pc.updated_at AS reviewed_at
            FROM post_candidate_photos pcp
            JOIN post_candidates pc
              ON pc.id = pcp.post_id
            WHERE pcp.photo_id IN ({placeholders})
              AND pc.editorial_feedback IS NOT NULL
              AND trim(pc.editorial_feedback) != ''
              AND pc.status IN (
                  'recurate',
                  'rejected'
              )
            ORDER BY
                pcp.photo_id,
                pc.updated_at DESC,
                pc.id DESC
            """,
            photo_ids,
        ).fetchall()

    feedback_by_photo: dict[
        int,
        list[dict],
    ] = {}

    for feedback_row in feedback_rows:
        photo_id = feedback_row["photo_id"]

        history = feedback_by_photo.setdefault(
            photo_id,
            [],
        )

        if len(history) >= 3:
            continue

        history.append(
            {
                "candidate_id":
                    feedback_row["candidate_id"],
                "candidate_title":
                    feedback_row["candidate_title"],
                "candidate_status":
                    feedback_row["candidate_status"],
                "feedback":
                    feedback_row["editorial_feedback"],
                "reviewed_at":
                    feedback_row["reviewed_at"],
            }
        )

    result = []

    for row in rows:
        analysis = (
            json.loads(row["analysis_json"])
            if row["analysis_json"]
            else {}
        )

        scores = (
            json.loads(row["scores_json"])
            if row["scores_json"]
            else {}
        )

        result.append(
            {
                "photo_id": row["id"],
                "filename": row["filename"],
                "captured_at": row["captured_at"],
                "curation_state": (
                    row["curation_state"]
                    or "eligible"
                ),
                "recuration_priority": (
                    row["curation_state"]
                    == "recurate"
                ),
                "prior_editorial_feedback":
                    feedback_by_photo.get(
                        row["id"],
                        [],
                    ),
                "latitude": row["latitude"],
                "longitude": row["longitude"],
                "operation": row["operation_label"],
                "operation_disposition": row[
                    "operation_disposition"
                ],
                "scene": row["scene_label"],
                "event": row["event_label"],
                "event_scope": row["event_scope"],
                "description": analysis.get(
                    "description"
                ),
                "scores": scores,
                "times_published": row[
                    "times_published"
                ],
            }
        )

    return result


def fingerprint(
    photo_ids: list[int],
) -> str:
    material = (
        f"v{DISCOVERY_VERSION}:"
        + ",".join(
            str(x)
            for x in sorted(photo_ids)
        )
    )

    digest = hashlib.sha256(
        material.encode()
    ).hexdigest()

    return (
        f"discovery:{digest}"
    )


def build_prompt(
    context: list[dict],
) -> str:
    data = json.dumps(
        context,
        indent=2,
        ensure_ascii=False,
    )

    return f"""
You are the POST DISCOVERY editor for a personal
photo library.

You are NOT composing the final Instagram carousel
yet.

Your job is to inspect organized photographs and
identify promising sets that deserve a full post
curation pass.

The photographs already contain context from:
- individual image analysis,
- photographic operations,
- scenes,
- events,
- metadata,
- prior publication history.

CRITICAL PRINCIPLE:

USER RECURATION SIGNAL:

A photograph with
"recuration_priority": true
was explicitly marked by the user as deserving
another serious curation attempt.

Give these photographs deliberate consideration
when identifying promising post ideas.

Do NOT force a recurated photograph into a post
when it does not genuinely fit. The user signal
means "try again thoughtfully", not "must publish".

A photograph whose curation state is suppressed
should never be present in this input. If one is
ever encountered, ignore it completely.

HUMAN EDITORIAL FEEDBACK:

A photograph may include a
"prior_editorial_feedback" list.

Each entry represents direct human feedback from a
previous candidate containing that photograph. It
may include:

- candidate_id,
- candidate_title,
- candidate_status,
- feedback,
- reviewed_at.

Treat this as high-value editorial context.

Use the feedback to understand why an earlier
selection, grouping, story, or concept did not work
and what the human editor wants reconsidered.

Feedback attached to one photograph may describe
the previous candidate as a whole, so do not assume
every sentence applies only to that individual image.

Do not mechanically rebuild a failed candidate.
Combine the editor's guidance with the actual visual
material and the wider library to find a genuinely
better curation.

When multiple feedback entries exist, generally give
greater weight to newer feedback while still using
older entries when they provide useful context.

TIME IS CONTEXT, NOT A POST BOUNDARY.

Photos close in time may naturally belong together,
but chronology is only one possible editorial logic.

A worthwhile post may represent:

- one photograph,
- one photographic operation,
- one specific event,
- one outing,
- one day,
- several days,
- a broader period,
- a recurring subject,
- an aesthetic pattern,
- a theme,
- a message,
- a visual contrast,
- a retrospective,
- or another editorial idea.

Examples:

Photos from one dinner might justify a focused post.

Photos from an entire day might tell one coherent
story.

Photos from several unrelated dates might form a much
better post because they share fashion, architecture,
trains, nightlife, color, emotion, humor, or another
theme.

Two photos taken one minute apart might NOT belong in
the same post.

Metadata such as:
- capture time,
- same day,
- same week,
- same month,
- GPS,
- filename sequence,
- device/source

should influence your reasoning, but must NEVER force
the result.

Scene and event labels are also contextual evidence.
They are NOT mandatory post boundaries.

Be selective.

Do not manufacture many overlapping posts simply
because combinations are possible.

Do not propose trivial variants of the same candidate.

Prefer a smaller number of genuinely useful editorial
ideas.

Photos marked operation_disposition="abandon" should
normally be excluded, but they may be included if
their context gives them a specific story, humor, or
sequence purpose.

Previously published photos should normally be
avoided. Reuse requires a clear retrospective,
comparison, callback, or other deliberate reason.

Return at most 10 proposals.

A proposal may contain only one photo.

Return ONLY valid JSON:

{{
  "proposals": [
    {{
      "label": "...",
      "discovery_type": "event|day|multi_day|theme|aesthetic|message|retrospective|single|other",
      "photo_ids": [1, 2, 3],
      "reasoning": "...",
      "strength": 0.87
    }}
  ]
}}

strength ranges from 0.0 to 1.0.

Do not determine final carousel order.
Do not determine final cover.
Do not write captions.

Those are later stages.

If nothing here deserves a post, return:

{{
  "proposals": []
}}

PHOTO LIBRARY CONTEXT:

{data}
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

    return json.loads(cleaned)


def validate(
    result: dict,
    available_ids: set[int],
) -> None:
    if "proposals" not in result:
        raise ValueError(
            "Missing proposals"
        )

    seen_sets = set()

    for proposal in result["proposals"]:
        ids = [
            int(x)
            for x in proposal["photo_ids"]
        ]

        if not ids:
            raise ValueError(
                "Empty post proposal"
            )

        if len(ids) != len(set(ids)):
            raise ValueError(
                "Duplicate photo in proposal"
            )

        unknown = (
            set(ids)
            - available_ids
        )

        if unknown:
            raise ValueError(
                f"Unknown photo IDs: "
                f"{sorted(unknown)}"
            )

        strength = float(
            proposal["strength"]
        )

        if not 0 <= strength <= 1:
            raise ValueError(
                "Invalid proposal strength"
            )

        key = tuple(
            sorted(ids)
        )

        if key in seen_sets:
            raise ValueError(
                "Duplicate proposal photo set"
            )

        seen_sets.add(key)


def discovery_already_run(
    photo_ids: list[int],
) -> bool:
    fp = fingerprint(
        photo_ids
    )

    with connect() as db:
        row = db.execute(
            """
            SELECT id
            FROM ai_runs
            WHERE stage = 'post_group_discovery'
              AND input_fingerprint = ?
            LIMIT 1
            """,
            (fp,),
        ).fetchone()

    return row is not None


def discover_post_groups(
    photo_ids: list[int],
) -> dict:
    load_dotenv(
        ".env",
        override=True,
    )

    context = load_context(
        photo_ids
    )

    found = {
        x["photo_id"]
        for x in context
    }

    expected = set(
        photo_ids
    )

    if found != expected:
        raise ValueError(
            "Post discovery context is missing "
            f"photos: {sorted(expected - found)}"
        )

    model = os.environ[
        "CURATOR_CURATION_MODEL"
    ]

    client = OpenAI(
        api_key=os.environ[
            "OPENAI_API_KEY"
        ]
    )

    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": build_prompt(
                            context
                        ),
                    }
                ],
            }
        ],
    )

    result = parse_json(
        response.output_text
    )

    validate(
        result,
        expected,
    )

    fp = fingerprint(
        photo_ids
    )

    with connect() as db:
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
                "post_group_discovery",
                model,
                PROMPT_VERSION,
                DISCOVERY_VERSION,
                fp,
                json.dumps(
                    result,
                    ensure_ascii=False,
                ),
            ),
        )

        db.commit()

    return {
        "model": model,
        "fingerprint": fp,
        "result": result,
    }
