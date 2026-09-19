#!/bin/bash
# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

set -euo pipefail

cd "$(dirname "$0")/.."

source .venv/bin/activate

exec python -m \
    curator.ingest.icloud_link \
    "$@"
