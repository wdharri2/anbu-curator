# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PhotoScores(BaseModel):
    aura: float | None = Field(default=None, ge=1, le=10)
    fun: float | None = Field(default=None, ge=1, le=10)
    humor: float | None = Field(default=None, ge=1, le=10)

    photography_quality: float | None = Field(
        default=None,
        ge=1,
        le=10,
    )

    life_significance: float | None = Field(
        default=None,
        ge=1,
        le=10,
    )

    story_value: float | None = Field(
        default=None,
        ge=1,
        le=10,
    )

    novelty: float | None = Field(
        default=None,
        ge=1,
        le=10,
    )

    cover_potential: float | None = Field(
        default=None,
        ge=1,
        le=10,
    )


class PostScores(BaseModel):
    aura: float | None = Field(default=None, ge=1, le=10)
    fun: float | None = Field(default=None, ge=1, le=10)
    humor: float | None = Field(default=None, ge=1, le=10)

    photography_quality: float | None = Field(
        default=None,
        ge=1,
        le=10,
    )

    life_significance: float | None = Field(
        default=None,
        ge=1,
        le=10,
    )

    cohesion: float | None = Field(
        default=None,
        ge=1,
        le=10,
    )

    feed_value: float | None = Field(
        default=None,
        ge=1,
        le=10,
    )

    immediacy: int | None = Field(
        default=None,
        ge=1,
        le=4,
    )


OperationDecision = Literal[
    "abandon",
    "single",
    "multiple",
    "sequence",
    "reserve",
]


PhotoRole = Literal[
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
]


PostType = Literal[
    "notification",
    "moment",
    "focused_subject",
    "day_or_outing",
    "vibe",
    "retrospective",
    "collection",
    "other",
]
