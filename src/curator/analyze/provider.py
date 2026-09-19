# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path

from dotenv import load_dotenv


class VisionProvider(ABC):
    @abstractmethod
    def analyze_image(
        self,
        image_path: Path,
        prompt: str,
        *,
        detail: str = "low",
    ) -> str:
        raise NotImplementedError


def get_provider(
    model_env: str = "CURATOR_ANALYSIS_MODEL",
) -> VisionProvider:
    load_dotenv(".env", override=True)

    provider = os.getenv(
        "CURATOR_AI_PROVIDER",
        "openai",
    )

    model = os.environ[model_env]

    if provider == "openai":
        from curator.analyze.openai_provider import (
            OpenAIVisionProvider,
        )

        return OpenAIVisionProvider(
            model=model,
        )

    raise RuntimeError(
        f"Unknown AI provider: {provider}"
    )
