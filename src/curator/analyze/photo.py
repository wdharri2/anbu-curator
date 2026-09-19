# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageOps

from curator.analyze.provider import get_provider
from curator.analyze.crop import normalize_crop_candidate
from curator.config import load_settings
from curator.db import connect
from curator.storage.lifecycle import visual_path
from curator.models.schemas import PhotoScores


ANALYSIS_VERSION = 2
PROMPT_VERSION = 2


def fetch_photo(photo_id: int) -> dict:
    with connect() as db:
        row = db.execute(
            """
            SELECT id, filename, work_path, captured_at
            FROM photos
            WHERE id = ?
            """,
            (photo_id,),
        ).fetchone()

    if row is None:
        raise ValueError(f"Photo {photo_id} not found")

    return dict(row)


def prepare_analysis_image(photo: dict) -> Path:
    settings = load_settings()

    source_path = visual_path(photo["id"])
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    out_dir = settings.cache / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"photo-{photo['id']}-analysis.jpg"

    with Image.open(source_path) as image:
        # Analyze the image in its intended visual
        # orientation. Crop coordinates are relative
        # to this EXIF-oriented image.
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGB")
        image.thumbnail((1600, 1600))
        image.save(
            out_path,
            format="JPEG",
            quality=90,
            optimize=True,
        )

    return out_path


def build_prompt(photo: dict) -> str:
    return f"""
You are analyzing a single photograph for an AI-assisted Instagram curation system.

This is PHOTO ID {photo["id"]}.
Filename: {photo["filename"]}
Captured at: {photo["captured_at"]}

Return only valid JSON.
Do not use markdown fences.
Do not include any extra commentary.

The JSON must have exactly this top-level structure:

{{
  "description": "...",
  "content": {{
    "people_count": 0,
    "setting": "...",
    "shot_type": "...",
    "primary_subject": "...",
    "notable_elements": ["...", "..."]
  }},
  "quality_flags": {{
    "blurry": false,
    "awkward_expression": false,
    "eyes_closed": false,
    "duplicate_likelihood_note": "...",
    "cropping_issue": false,
    "lighting_issue": false
  }},
  "scores": {{
    "aura": 1.0,
    "fun": 1.0,
    "humor": 1.0,
    "photography_quality": 1.0,
    "life_significance": 1.0,
    "story_value": 1.0,
    "novelty": 1.0,
    "cover_potential": 1.0
  }},
  "crop": {{
    "recommended": false,
    "confidence": 0.0,
    "purpose": null,
    "reason": null,
    "preferred_aspect_ratio": null,
    "box": null
  }}
}}

Scoring rubric:
- aura: charisma, presence, coolness, overall magnetism
- fun: how playful or enjoyable the image feels
- humor: comedic value
- photography_quality: composition, clarity, lighting, timing
- life_significance: how meaningful or milestone-like the depicted moment seems
- story_value: usefulness in telling the story of an outing/event/post
- novelty: distinctiveness compared to what people usually post
- cover_potential: how strong this image would be as the first image in an Instagram carousel

Crop analysis:
- "crop" is an editorial suggestion only. Never assume the original should be permanently changed.
- recommended should be true only when cropping would meaningfully improve posting usefulness.
- purpose should normally be one of:
  "composition",
  "cleanup",
  "remove_ui",
  "subject_emphasis",
  "aspect_ratio",
  "straighten_and_crop",
  or "other".
- For screenshots, reposts, or story captures, distinguish meaningful content from app UI, usernames, controls, borders, blank margins, and other interface chrome.
- Consider whether removing story/app UI would make the image feel like a normal postable image.
- Consider distracting objects near the edges, excessive empty space, weak subject placement, and framing that is substantially improved by tightening.
- Consider whether 4:5, 1:1, 3:4, or another aspect ratio would make the image more useful in a carousel.
- Do not recommend a crop merely because Instagram supports a particular aspect ratio.
- Do not crop out meaningful people, text, context, or visual information just to force a standard shape.
- A future post curator may choose a different crop to make several carousel images work together. This recommendation should identify a strong standalone crop candidate without assuming knowledge of the other carousel images.
- box describes the RETAINED portion of the image, not the part to remove.
- box coordinates are normalized from 0.0 to 1.0 relative to the EXIF-oriented displayed image:
  x = left edge
  y = top edge
  width = retained width
  height = retained height
- Example full image:
  {{"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}}
- If recommended is false, use box=null unless there is a genuinely useful optional crop worth preserving.
- confidence must be between 0.0 and 1.0.
- preferred_aspect_ratio should be a string such as "4:5", "1:1", "3:4", "9:16", or null.
- Keep the reason concise and specific.

Use 1.0 to 10.0 scale with one decimal place.
Be thoughtful but concise.
Do not invent specific identities if uncertain.
""".strip()


def strip_json_fences(text: str) -> str:
    cleaned = text.strip()

    if cleaned.startswith("```"):
        lines = cleaned.splitlines()

        if lines and lines[0].startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]

        cleaned = "\n".join(lines).strip()

    return cleaned


def parse_response(text: str) -> dict:
    cleaned = strip_json_fences(text)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")

        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start:end + 1])

        raise


def save_analysis(
    photo_id: int,
    response_text: str,
    parsed: dict,
    model: str,
) -> None:
    # Keep crop data structurally consistent even if
    # the model omits optional fields or returns a
    # slightly invalid normalized crop box.
    parsed = dict(parsed)

    parsed["crop"] = normalize_crop_candidate(
        parsed.get("crop")
    )

    scores = PhotoScores(**parsed["scores"])

    with connect() as db:
        db.execute(
            """
            UPDATE photos
            SET analysis_status = 'done',
                analysis_json = ?,
                scores_json = ?,
                analysis_model = ?,
                analysis_version = ?,
                prompt_version = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                json.dumps(parsed, ensure_ascii=False),
                json.dumps(scores.model_dump(), ensure_ascii=False),
                model,
                ANALYSIS_VERSION,
                PROMPT_VERSION,
                photo_id,
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
                "photo_analysis",
                model,
                PROMPT_VERSION,
                ANALYSIS_VERSION,
                f"photo:{photo_id}",
                json.dumps(
                    {
                        "raw_response": response_text,
                        "parsed": parsed,
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        db.commit()


def analyze_photo(photo_id: int) -> dict:
    photo = fetch_photo(photo_id)
    analysis_image = prepare_analysis_image(photo)
    prompt = build_prompt(photo)

    provider = get_provider()
    response_text = provider.analyze_image(
        analysis_image,
        prompt,
    )

    parsed = parse_response(response_text)

    save_analysis(
        photo_id=photo_id,
        response_text=response_text,
        parsed=parsed,
        model=provider.model,
    )

    return {
        "photo_id": photo_id,
        "analysis_image": str(analysis_image),
        "model": provider.model,
        "parsed": parsed,
    }
