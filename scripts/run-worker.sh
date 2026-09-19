#!/usr/bin/env bash
# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$PROJECT"

exec "$PROJECT/.venv/bin/python" \
    -m curator.ingest.worker
