# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import hashlib
import json
import os

from dotenv import load_dotenv
from openai import OpenAI

from curator.db import connect


ARBITRATION_VERSION = 5
PROMPT_VERSION = 4

DECISIONS = {
    "select",
    "replace",
    "alternative",
    "reject",
}


def latest_discovery() -> dict:
    with connect() as db:
        row = db.execute(
            """
            SELECT
                id,
                output_json
            FROM ai_runs
            WHERE stage = 'post_group_discovery'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    if not row:
        raise RuntimeError(
            "No post discovery run exists"
        )

    return {
        "run_id": row["id"],
        "result": json.loads(
            row["output_json"]
        ),
    }


def existing_candidates() -> list[dict]:
    with connect() as db:
        candidates = db.execute(
            """
            SELECT
                id,
                title,
                post_type,
                status,
                story_summary,
                curator_reasoning
            FROM post_candidates
            WHERE status IN (
                'candidate',
                'published'
            )
            ORDER BY id
            """
        ).fetchall()

        result = []

        for candidate in candidates:
            photos = db.execute(
                """
                SELECT
                    photo_id,
                    position
                FROM post_candidate_photos
                WHERE post_id = ?
                ORDER BY position
                """,
                (candidate["id"],),
            ).fetchall()

            result.append(
                {
                    "candidate_id": candidate["id"],
                    "title": candidate["title"],
                    "post_type": candidate["post_type"],
                    "status": candidate["status"],
                    "story_summary": candidate[
                        "story_summary"
                    ],
                    "photo_ids": [
                        row["photo_id"]
                        for row in photos
                    ],
                }
            )

    return result


def load_editorial_history() -> list[dict]:
    with connect() as db:
        candidates = db.execute(
            """
            SELECT
                id,
                title,
                post_type,
                status,
                story_summary,
                curator_reasoning,
                editorial_feedback,
                updated_at
            FROM post_candidates
            WHERE status IN (
                'rejected',
                'recurate'
            )
            ORDER BY
                updated_at DESC,
                id DESC
            """
        ).fetchall()

        result = []

        for candidate in candidates:
            photos = db.execute(
                """
                SELECT
                    photo_id,
                    position,
                    review_disposition
                FROM post_candidate_photos
                WHERE post_id = ?
                ORDER BY position
                """,
                (candidate["id"],),
            ).fetchall()

            result.append(
                {
                    "candidate_id":
                        candidate["id"],
                    "title":
                        candidate["title"],
                    "post_type":
                        candidate["post_type"],
                    "status":
                        candidate["status"],
                    "story_summary":
                        candidate["story_summary"],
                    "curator_reasoning":
                        candidate[
                            "curator_reasoning"
                        ],
                    "editorial_feedback":
                        candidate[
                            "editorial_feedback"
                        ],
                    "reviewed_at":
                        candidate["updated_at"],
                    "photos": [
                        {
                            "photo_id":
                                row["photo_id"],
                            "review_disposition":
                                row[
                                    "review_disposition"
                                ],
                        }
                        for row in photos
                    ],
                }
            )

    return result


def normalize_proposals(
    proposals: list[dict],
    candidates: list[dict],
) -> list[dict]:
    result = []

    for index, proposal in enumerate(
        proposals,
        start=1,
    ):
        ids = sorted(
            int(x)
            for x in proposal["photo_ids"]
        )

        exact_existing = []

        for candidate in candidates:
            candidate_ids = sorted(
                int(x)
                for x in candidate["photo_ids"]
            )

            if ids == candidate_ids:
                exact_existing.append(
                    candidate["candidate_id"]
                )

        result.append(
            {
                "proposal_index": index,
                "label": proposal["label"],
                "discovery_type": proposal[
                    "discovery_type"
                ],
                "strength": float(
                    proposal["strength"]
                ),
                "photo_ids": ids,
                "reasoning": proposal["reasoning"],
                "exact_existing_candidate_ids": (
                    exact_existing
                ),
            }
        )

    return result


def build_prompt(
    proposals: list[dict],
    candidates: list[dict],
    editorial_history: list[dict],
) -> str:
    return f"""
You are the PORTFOLIO EDITOR for a personal
Instagram curation system.

The discovery editor has proposed possible posts.

You must decide which ideas should ACTUALLY enter
the post queue.

Do not judge every idea independently.

Think about the portfolio as a whole.

Allowed decisions:

select
    This idea is sufficiently strong and distinct
    to proceed to full post curation now.

replace
    This idea is a better, broader, or more faithful
    treatment of material already represented by one
    or more ACTIVE candidate drafts. Create this idea
    and supersede those narrower active drafts.

    Use replaces_candidate_ids to identify exactly
    which active candidate drafts should be superseded.

    NEVER replace a published post automatically.

alternative
    This is a legitimate editorial possibility,
    but it competes with another selected proposal
    or an existing candidate. Preserve the idea in
    editorial history without creating another post.

reject
    This idea is weak, redundant, derivative, or
    unnecessary.

CRITICAL RULES:

1. Prefer a small portfolio of strong distinct posts.

2. Do not flood the queue with different framings of
   substantially the same photographs.

3. Exact duplicate photo sets must never create
   another candidate.

4. Subset and superset proposals usually compete.

5. A different title does not make a highly
   overlapping post distinct.

6. Overlap itself is not forbidden. Two posts may
   share photographs when their editorial purposes
   are genuinely different enough to justify both.

7. A single-image post can coexist with a larger
   event post when that single image genuinely has
   an independent reason to be posted.

8. EXISTING PORTFOLIO contains only active
   candidate or published posts. These are real
   portfolio occupants and may block redundant
   proposals.

9. EDITORIAL HISTORY contains rejected or recurated
   prior candidates, their photo-level review
   dispositions, and direct human feedback when
   provided. A reject/recurate decision is itself a
   useful editorial signal even without a written
   note. These are NOT active portfolio occupants and must
   not block a better replacement merely because the
   old candidate existed.

10. Treat human editorial feedback as high-value
    instruction about the editor's preferences.
    Apply it across later discovery windows when the
    same experience, subject, scene, outing, or
    grouping problem appears again.

11. If prior feedback says several similar posts
    should be consolidated, avoid admitting more
    fragmented versions in successive windows.
    Prefer a stronger broader proposal that satisfies
    that feedback.

12. A recurated candidate means the previous draft
    was not satisfactory. Do not protect that old
    draft from replacement.

13. A rejected candidate means that specific concept
    was rejected. Its useful photographs may still
    support a materially different proposal when the
    feedback indicates how they should be reused.

14. Time, event, scene and trip context are evidence,
    not mandatory post boundaries.

15. When a later proposal is explicitly a better
    consolidated replacement for an active candidate,
    use replace rather than select. Do not keep both
    drafts active merely because their exact photo
    sets differ.

16. Different rooms, installations, scenes, or visual
    treatments within one continuous experience do
    not automatically justify separate posts.
    Consider event overlap, capture-time continuity,
    human editorial feedback, and the larger story.

17. replace may target ACTIVE candidate drafts only.
    Never automatically replace a published post.

18. Do not choose covers.
    Do not write captions.
    Do not construct the final carousel.

Return ONLY valid JSON:

{{
  "portfolio_reasoning": "...",
  "decisions": [
    {{
      "proposal_index": 1,
      "decision": "select|replace|alternative|reject",
      "reason": "...",
      "competes_with_proposals": [],
      "competes_with_existing_candidates": [],
      "replaces_candidate_ids": []
    }}
  ]
}}

Every proposal must receive exactly one decision.

PROPOSALS:

{json.dumps(
    proposals,
    indent=2,
    ensure_ascii=False,
)}

EXISTING PORTFOLIO:

{json.dumps(
    candidates,
    indent=2,
    ensure_ascii=False,
)}

EDITORIAL HISTORY:

{json.dumps(
    editorial_history,
    indent=2,
    ensure_ascii=False,
)}
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
        cleaned = cleaned[start:end + 1]

    return json.loads(cleaned)


def validate(
    result: dict,
    proposal_count: int,
    candidates: list[dict],
) -> None:
    decisions = result.get(
        "decisions",
        []
    )

    indexes = []

    candidate_statuses = {
        int(candidate["candidate_id"]):
            candidate["status"]
        for candidate in candidates
    }

    for item in decisions:
        index = int(
            item["proposal_index"]
        )

        indexes.append(index)

        if item["decision"] not in DECISIONS:
            raise ValueError(
                "Invalid arbitration decision: "
                f"{item['decision']}"
            )

        replacement_ids = [
            int(x)
            for x in item.get(
                "replaces_candidate_ids",
                [],
            )
        ]

        if len(replacement_ids) != len(
            set(replacement_ids)
        ):
            raise ValueError(
                "Duplicate replacement candidate ID"
            )

        if item["decision"] == "replace":
            if not replacement_ids:
                raise ValueError(
                    "replace requires "
                    "replaces_candidate_ids"
                )

            for candidate_id in replacement_ids:
                status = candidate_statuses.get(
                    candidate_id
                )

                if status is None:
                    raise ValueError(
                        "Replacement references "
                        "unknown candidate "
                        f"{candidate_id}"
                    )

                if status != "candidate":
                    raise ValueError(
                        "Only active candidate drafts "
                        "may be replaced; "
                        f"{candidate_id} is {status}"
                    )

        elif replacement_ids:
            raise ValueError(
                "replaces_candidate_ids may only "
                "be used with decision=replace"
            )

    expected = set(
        range(
            1,
            proposal_count + 1,
        )
    )

    if set(indexes) != expected:
        raise ValueError(
            "Arbitration did not decide "
            "every proposal"
        )

    if len(indexes) != len(set(indexes)):
        raise ValueError(
            "Duplicate arbitration decision"
        )


def enforce_exact_duplicates(
    result: dict,
    proposals: list[dict],
) -> None:
    proposal_map = {
        p["proposal_index"]: p
        for p in proposals
    }

    for decision in result["decisions"]:
        proposal = proposal_map[
            int(
                decision["proposal_index"]
            )
        ]

        existing = proposal[
            "exact_existing_candidate_ids"
        ]

        if not existing:
            continue

        decision["decision"] = "reject"
        decision["replaces_candidate_ids"] = []

        competition = set(
            decision.get(
                "competes_with_existing_candidates",
                [],
            )
        )

        competition.update(existing)

        decision[
            "competes_with_existing_candidates"
        ] = sorted(competition)

        decision["reason"] = (
            "Exact photo-set duplicate of "
            "existing candidate(s) "
            + ", ".join(
                str(x)
                for x in existing
            )
            + ". "
            + decision.get(
                "reason",
                "",
            )
        ).strip()


def fingerprint(
    proposals: list[dict],
    candidates: list[dict],
    editorial_history: list[dict],
) -> str:
    payload = json.dumps(
        {
            "proposals": proposals,
            "candidates": candidates,
            "editorial_history":
                editorial_history,
        },
        sort_keys=True,
        ensure_ascii=False,
    )

    digest = hashlib.sha256(
        payload.encode()
    ).hexdigest()

    return f"portfolio:{digest}"


def arbitrate_proposals(
    raw_proposals: list[dict],
    *,
    discovery_run_id: int | None = None,
) -> dict:
    candidates = existing_candidates()
    editorial_history = (
        load_editorial_history()
    )

    proposals = normalize_proposals(
        raw_proposals,
        candidates,
    )

    if not proposals:
        return {
            "discovery_run_id": discovery_run_id,
            "proposals": [],
            "existing_candidates": candidates,
            "arbitration": {
                "portfolio_reasoning": (
                    "No proposals to arbitrate."
                ),
                "decisions": [],
            },
        }

    load_dotenv(
        ".env",
        override=True,
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
                            proposals,
                            candidates,
                            editorial_history,
                        ),
                    }
                ],
            }
        ],
    )

    arbitration = parse_json(
        response.output_text
    )

    validate(
        arbitration,
        len(proposals),
        candidates,
    )

    # Deterministic safety net.
    # AI discretion can never accidentally recreate
    # an exact existing candidate.
    enforce_exact_duplicates(
        arbitration,
        proposals,
    )

    stored = {
        "discovery_run_id": discovery_run_id,
        "proposals": proposals,
        "existing_candidates": candidates,
        "editorial_history":
            editorial_history,
        "arbitration": arbitration,
    }

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
                "post_portfolio_arbitration",
                model,
                PROMPT_VERSION,
                ARBITRATION_VERSION,
                fingerprint(
                    proposals,
                    candidates,
                    editorial_history,
                ),
                json.dumps(
                    stored,
                    ensure_ascii=False,
                ),
            ),
        )

        db.commit()

    return stored


def arbitrate_latest() -> dict:
    discovery = latest_discovery()

    return arbitrate_proposals(
        discovery[
            "result"
        ].get(
            "proposals",
            [],
        ),
        discovery_run_id=discovery[
            "run_id"
        ],
    )
