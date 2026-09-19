# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import argparse
import json

from curator.organize.operations import (
    group_operations,
)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "photo_ids",
        nargs="+",
        type=int,
    )

    args = parser.parse_args()

    result = group_operations(
        args.photo_ids
    )

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
