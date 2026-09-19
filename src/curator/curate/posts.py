# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import json
import os

from dotenv import load_dotenv
from openai import OpenAI

from curator.db import connect
from curator.models.schemas import PostScores


CURATOR_VERSION = 1
PROMPT_VERSION = 1

POST_TYPES = {
    "notification",
    "moment",
    "focused_subject",
    "day_or_outing",
    "vibe",
    "retrospective",
    "collection",
    "other",
}

PHOTO_ROLES = {
    "cover",
    "establishing",
    "portrait",
    "candid",
    "detail",
    "humor",
    "transition",
    "context",
    "climax",
    "closer",
}


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
                o.id AS operation_id,
                o.label AS operation_label,
                o.description AS operation_description,

                op.photo_id,
                op.decision AS disposition,
                op.operation_rank,
                op.decision_reason,

                p.filename,
                p.captured_at,
                p.analysis_json,
                p.scores_json,

                (
                    SELECT COUNT(*)
                    FROM published_post_photos ppp
                    WHERE ppp.photo_id = p.id
                ) AS times_published,

                (
                    SELECT MAX(pp.posted_at)
                    FROM published_post_photos ppp
                    JOIN published_posts pp
                      ON pp.id = ppp.published_post_id
                    WHERE ppp.photo_id = p.id
                ) AS last_published_at

            FROM operations o

            JOIN operation_photos op
              ON op.operation_id = o.id

            JOIN photos p
              ON p.id = op.photo_id

            WHERE p.id IN ({placeholders})

            ORDER BY
                o.id,
                op.operation_rank
            """,
            photo_ids,
        ).fetchall()

    operations: dict[int, dict] = {}

    for row in rows:
        op_id = row["operation_id"]

        if op_id not in operations:
            operations[op_id] = {
                "operation_id": op_id,
                "label": row["operation_label"],
                "description": row[
                    "operation_description"
                ],
                "photos": [],
            }

        analysis = json.loads(
            row["analysis_json"]
        )

        scores = json.loads(
            row["scores_json"]
        )

        operations[op_id]["photos"].append(
            {
                "photo_id": row["photo_id"],
                "filename": row["filename"],
                "captured_at": row["captured_at"],
                "operation_disposition": row[
                    "disposition"
                ],
                "operation_rank": row[
                    "operation_rank"
                ],
                "decision_reason": row[
                    "decision_reason"
                ],
                "description": analysis.get(
                    "description"
                ),
                "scores": scores,
                "publication_history": {
                    "times_published": row[
                        "times_published"
                    ],
                    "last_published_at": row[
                        "last_published_at"
                    ],
                },
            }
        )

    return list(operations.values())


def build_prompt(
    operations: list[dict],
) -> str:
    context = json.dumps(
        operations,
        indent=2,
        ensure_ascii=False,
    )

    return f"""
You are the POST EDITOR for a personal Instagram
photo-curation system.

An earlier stage has already:

1. analyzed every image,
2. grouped redundant images into photographic
   operations,
3. identified primary images and weak alternates.

Your job is now to determine whether these
operations naturally form a worthwhile Instagram
post.

Do NOT force a post because material exists.

POST LENGTH IS AN EDITORIAL OUTPUT.

A post may naturally be:
- one image,
- a short carousel,
- a longer outing/story carousel.

Every additional image must justify another swipe.

You may combine different photographic operations
when they form a coherent outing, story, vibe, or
life moment.

Do not resurrect an image marked "abandon" unless
there is an unusually compelling contextual reason.

PUBLICATION HISTORY IS IMPORTANT.

A photo with times_published > 0 has already appeared
on the account.

Normally avoid reusing it because accidental repeat
posting is undesirable.

However this is NOT an absolute ban. Reuse can be
appropriate for something such as:
- retrospective,
- comparison,
- anniversary,
- major life update,
- deliberate callback.

If you reuse an already-published photo, explicitly
justify why.

Do NOT make the final profile/feed-aware cover choice
yet.

Instead nominate up to 3 reasonable cover candidates.
A later stage will choose among them using the
Instagram profile grid.

Allowed post types:
notification
moment
focused_subject
day_or_outing
vibe
retrospective
collection
other

Allowed photo roles:
cover
establishing
portrait
candid
detail
humor
transition
context
climax
closer

Return ONLY valid JSON in this shape:

{{
  "make_post": true,
  "title": "...",
  "post_type": "day_or_outing",
  "story_summary": "...",
  "curator_reasoning": "...",

  "photos": [
    {{
      "photo_id": 12,
      "position": 1,
      "role": "establishing",
      "inclusion_reason": "..."
    }}
  ],

  "excluded_survivors": [
    {{
      "photo_id": 8,
      "reason": "..."
    }}
  ],

  "cover_candidates": [
    {{
      "photo_id": 12,
      "reason": "..."
    }}
  ],

  "scores": {{
    "aura": 1.0,
    "fun": 1.0,
    "humor": 1.0,
    "photography_quality": 1.0,
    "life_significance": 1.0,
    "cohesion": 1.0,
    "feed_value": 1.0,
    "immediacy": 1
  }}
}}

If no worthwhile post exists:
- set make_post to false,
- use an empty photos array,
- explain why.

Post-level scores are holistic.
Do NOT just average photo-level scores.

Immediacy:
1 = evergreen
2 = more valuable while this period is current
3 = timely event worth showing soon
4 = significant announcement/life update

MATERIAL:

{context}
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
    available_ids: set[int],
) -> None:
    if not result.get("make_post"):
        return

    if result["post_type"] not in POST_TYPES:
        raise ValueError(
            "Invalid post type"
        )

    selected = []

    for item in result["photos"]:
        photo_id = int(
            item["photo_id"]
        )

        selected.append(
            photo_id
        )

        if item["role"] not in PHOTO_ROLES:
            raise ValueError(
                f"Invalid role: {item['role']}"
            )

    if not selected:
        raise ValueError(
            "make_post=true but no photos selected"
        )

    if len(selected) != len(set(selected)):
        raise ValueError(
            "Duplicate photo selected"
        )

    unknown = (
        set(selected)
        - available_ids
    )

    if unknown:
        raise ValueError(
            f"Unknown photos: {unknown}"
        )

    positions = sorted(
        int(item["position"])
        for item in result["photos"]
    )

    expected = list(
        range(
            1,
            len(positions) + 1,
        )
    )

    if positions != expected:
        raise ValueError(
            "Carousel positions are not contiguous"
        )

    PostScores(
        **result["scores"]
    )


def save_candidate(
    result: dict,
    *,
    model: str,
    photo_ids: list[int],
) -> int | None:
    fingerprint = (
        "photos:"
        + ",".join(
            str(x)
            for x in sorted(photo_ids)
        )
    )

    if not result.get("make_post"):
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
                    "post_curation_no_post",
                    model,
                    PROMPT_VERSION,
                    CURATOR_VERSION,
                    fingerprint,
                    json.dumps(
                        result,
                        ensure_ascii=False,
                    ),
                ),
            )
            db.commit()

        return None

    scores = PostScores(
        **result["scores"]
    )

    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO post_candidates (
                title,
                post_type,
                status,
                story_summary,
                curator_reasoning,
                scores_json,
                curator_model,
                curator_version,
                prompt_version
            )
            VALUES (
                ?, ?, 'candidate',
                ?, ?, ?, ?, ?, ?
            )
            """,
            (
                result["title"],
                result["post_type"],
                result["story_summary"],
                result["curator_reasoning"],
                json.dumps(
                    scores.model_dump(),
                    ensure_ascii=False,
                ),
                model,
                CURATOR_VERSION,
                PROMPT_VERSION,
            ),
        )

        post_id = cursor.lastrowid

        cover_ids = {
            int(x["photo_id"])
            for x in result.get(
                "cover_candidates",
                []
            )
        }

        for item in sorted(
            result["photos"],
            key=lambda x: int(
                x["position"]
            ),
        ):
            photo_id = int(
                item["photo_id"]
            )

            db.execute(
                """
                INSERT INTO post_candidate_photos (
                    post_id,
                    photo_id,
                    position,
                    is_cover,
                    role,
                    inclusion_reason
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    post_id,
                    photo_id,
                    int(item["position"]),
                    1
                    if photo_id in cover_ids
                    else 0,
                    item["role"],
                    item["inclusion_reason"],
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
                "post_curation",
                model,
                PROMPT_VERSION,
                CURATOR_VERSION,
                fingerprint,
                json.dumps(
                    {
                        "candidate_id": post_id,
                        **result,
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        db.commit()

    return post_id


def curate_post(
    photo_ids: list[int],
) -> dict:
    load_dotenv(
        ".env",
        override=True,
    )

    operations = load_context(
        photo_ids
    )

    found_ids = {
        photo["photo_id"]
        for op in operations
        for photo in op["photos"]
    }

    missing = (
        set(photo_ids)
        - found_ids
    )

    if missing:
        raise ValueError(
            "Photos are missing operation "
            f"grouping: {sorted(missing)}"
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
                            operations
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
        set(photo_ids),
    )

    post_id = save_candidate(
        result,
        model=model,
        photo_ids=photo_ids,
    )

    return {
        "post_id": post_id,
        "model": model,
        "result": result,
    }
