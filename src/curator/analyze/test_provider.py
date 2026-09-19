# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

from curator.analyze.provider import get_provider


def main() -> None:
    provider = get_provider()

    print(
        "provider:",
        provider.__class__.__name__,
    )

    print(
        "model:",
        provider.model,
    )

    print("AI_PROVIDER_READY")


if __name__ == "__main__":
    main()
