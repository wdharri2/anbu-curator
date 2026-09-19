# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

from curator.storage.replicas import (
    human_bytes,
    inventory_all,
    print_report,
    report,
)


def main() -> None:
    print(
        "=== REFRESH REPLICA INVENTORY ==="
    )

    inventory_all()

    print()
    print(
        "=== STORAGE POLICY REPORT ==="
    )

    data = report()

    print_report(
        data
    )

    print()
    print(
        "effective_headroom="
        + human_bytes(
            data[
                "headroom_to_80_percent"
            ][
                "effective_two_copy_ingest"
            ]
        )
    )


if __name__ == "__main__":
    main()
