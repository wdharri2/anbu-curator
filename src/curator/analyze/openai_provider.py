# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from curator.analyze.provider import VisionProvider


class OpenAIVisionProvider(VisionProvider):
    def __init__(
        self,
        *,
        model: str | None = None,
    ) -> None:
        load_dotenv(".env", override=True)

        self.model = (
            model
            or os.environ["CURATOR_ANALYSIS_MODEL"]
        )

        self.client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"]
        )

    def _data_url(
        self,
        image_path: Path,
    ) -> str:
        mime, _ = mimetypes.guess_type(
            image_path.name
        )

        mime = mime or "image/jpeg"

        encoded = base64.b64encode(
            image_path.read_bytes()
        ).decode("ascii")

        return (
            f"data:{mime};base64,"
            f"{encoded}"
        )

    def analyze_image(
        self,
        image_path: Path,
        prompt: str,
        *,
        detail: str = "low",
    ) -> str:
        response = self.client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": prompt,
                        },
                        {
                            "type": "input_image",
                            "image_url": self._data_url(
                                image_path
                            ),
                            "detail": detail,
                        },
                    ],
                }
            ],
        )

        return response.output_text
