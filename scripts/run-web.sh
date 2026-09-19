#!/usr/bin/env bash
# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$PROJECT"

WG_IP=""

for _ in $(seq 1 60); do
    WG_IP="$(
        ip -4 -o addr show wg0 2>/dev/null \
        | awk '{print $4}' \
        | cut -d/ -f1 \
        | head -1
    )"

    if [ -n "$WG_IP" ]; then
        break
    fi

    sleep 2
done

if [ -z "$WG_IP" ]; then
    echo "wg0 IPv4 address unavailable"
    exit 1
fi

echo "Starting Photo Curator on ${WG_IP}:8080"

exec "$PROJECT/.venv/bin/uvicorn" \
    curator.review.web:app \
    --host "0.0.0.0" \
    --port 8080
