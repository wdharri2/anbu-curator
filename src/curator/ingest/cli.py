# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import argparse
import json
from pathlib import Path

from curator.ingest.manual import ingest_directory, ingest_file


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest photos into Photo Curator"
    )

    parser.add_argument(
        "path",
        type=Path,
        help="Photo file or directory to ingest",
    )

    args = parser.parse_args()

    if args.path.is_dir():
        results = ingest_directory(args.path)
    else:
        results = [ingest_file(args.path)]

    print(
        json.dumps(
            results,
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
