# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from curator.db import connect


def _clamp_unit(value: Any) -> float | None:
    if value is None:
        return None

    try:
        value = float(value)
    except (TypeError, ValueError):
        return None

    if value < 0:
        return 0.0

    if value > 1:
        return 1.0

    return value


def normalize_crop_candidate(
    crop: dict | None,
) -> dict:
    if not isinstance(crop, dict):
        return {
            "recommended": False,
            "confidence": None,
            "reason": None,
            "purpose": None,
            "preferred_aspect_ratio": None,
            "box": None,
        }

    recommended = bool(
        crop.get("recommended", False)
    )

    confidence = crop.get("confidence")
    try:
        confidence = (
            None
            if confidence is None
            else float(confidence)
        )
    except (TypeError, ValueError):
        confidence = None

    if confidence is not None:
        if confidence < 0:
            confidence = 0.0
        if confidence > 1:
            confidence = 1.0

    box = crop.get("box")

    if isinstance(box, dict):
        x = _clamp_unit(box.get("x"))
        y = _clamp_unit(box.get("y"))
        width = _clamp_unit(box.get("width"))
        height = _clamp_unit(box.get("height"))

        if None in (x, y, width, height):
            box = None
        elif width <= 0 or height <= 0:
            box = None
        else:
            if x + width > 1:
                width = max(0.0, 1.0 - x)
            if y + height > 1:
                height = max(0.0, 1.0 - y)

            if width <= 0 or height <= 0:
                box = None
            else:
                box = {
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                }
    else:
        box = None

    return {
        "recommended": recommended,
        "confidence": confidence,
        "reason": crop.get("reason"),
        "purpose": crop.get("purpose"),
        "preferred_aspect_ratio": crop.get(
            "preferred_aspect_ratio"
        ),
        "box": box,
    }


def load_photo_crop(
    photo_id: int,
) -> dict:
    with connect() as db:
        row = db.execute(
            """
            SELECT analysis_json
            FROM photos
            WHERE id = ?
            """,
            (photo_id,),
        ).fetchone()

    if not row or not row["analysis_json"]:
        return normalize_crop_candidate(None)

    analysis = json.loads(
        row["analysis_json"]
    )

    return normalize_crop_candidate(
        analysis.get("crop")
    )
