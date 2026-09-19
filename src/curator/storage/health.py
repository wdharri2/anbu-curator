# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from curator.config import (
    load_settings,
    validate_archive,
    validate_local_storage,
)


def storage_health() -> dict:
    settings = load_settings()

    local = validate_local_storage(settings)
    archive_ok, archive_message = validate_archive(settings)

    return {
        "environment": settings.environment,
        "local": local,
        "archive": {
            "ok": archive_ok,
            "target": settings.archive_uri,
            "message": archive_message,
        },
    }
