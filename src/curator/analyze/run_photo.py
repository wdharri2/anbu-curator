# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import argparse
import json

from curator.analyze.photo import analyze_photo


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze a single photo"
    )
    parser.add_argument(
        "photo_id",
        type=int,
        help="Photo ID to analyze",
    )
    args = parser.parse_args()

    result = analyze_photo(args.photo_id)

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
