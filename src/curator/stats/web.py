# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from curator.stats.service import build_overview


router = APIRouter()


STATS_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
/>
<title>Photo Curator Stats</title>
<style>
:root {
    color-scheme: dark;
}
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #0f1115;
    color: #e8eaed;
    margin: 0;
    padding: 20px;
}
h1, h2 {
    margin-top: 0;
}
.small {
    color: #9aa0a6;
    font-size: 0.95rem;
}
.grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 16px;
    margin-bottom: 20px;
}
.card {
    background: #171a21;
    border: 1px solid #2a2f3a;
    border-radius: 12px;
    padding: 16px;
    box-shadow: 0 2px 10px rgba(0,0,0,0.18);
}
.card h2 {
    font-size: 1rem;
    margin-bottom: 12px;
}
.row {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    padding: 6px 0;
    border-bottom: 1px solid #232833;
}
.row:last-child {
    border-bottom: none;
}
.label {
    color: #b8bec9;
}
.value {
    font-weight: 600;
    text-align: right;
    white-space: nowrap;
}
.ok {
    color: #7bd88f;
}
.warn {
    color: #ffd166;
}
.bad {
    color: #ff7b72;
}
pre {
    white-space: pre-wrap;
    word-break: break-word;
    margin: 0;
}
a {
    color: #8ab4f8;
}
</style>
</head>
<body>
    <h1>Photo Curator Stats</h1>
    <div class="small" id="generatedAt">
        Loading...
    </div>

    <div class="grid" id="cards"></div>

<script>
function fmtNumber(value) {
    if (value === null || value === undefined) {
        return "—";
    }
    return String(value);
}

function fmtRate(value, suffix = "") {
    if (value === null || value === undefined) {
        return "—";
    }
    return `${value}${suffix}`;
}

function fmtBytes(bytes) {
    if (bytes === null || bytes === undefined) {
        return "—";
    }

    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    let v = Number(bytes);

    while (v >= 1024 && i < units.length - 1) {
        v /= 1024;
        i++;
    }

    const digits = v >= 10 ? 0 : 1;
    return `${v.toFixed(digits)} ${units[i]}`;
}

function storageRows(item) {
    if (!item) {
        return `
            <div class="row"><div class="label">status</div><div class="value">—</div></div>
        `;
    }

    if (item.status && item.status !== "ok") {
        return `
            <div class="row"><div class="label">status</div><div class="value warn">${item.status}</div></div>
            <div class="row"><div class="label">target</div><div class="value">${item.target || "—"}</div></div>
            <div class="row"><div class="label">error</div><div class="value">${item.error || "—"}</div></div>
        `;
    }

    return `
        <div class="row"><div class="label">path</div><div class="value">${item.path || item.target || "—"}</div></div>
        <div class="row"><div class="label">used</div><div class="value">${fmtBytes(item.used_bytes)}</div></div>
        <div class="row"><div class="label">free</div><div class="value">${fmtBytes(item.free_bytes)}</div></div>
        <div class="row"><div class="label">total</div><div class="value">${fmtBytes(item.total_bytes)}</div></div>
        <div class="row"><div class="label">used %</div><div class="value">${fmtNumber(item.used_percent)}%</div></div>
    `;
}

function render(data) {
    document.getElementById("generatedAt").textContent =
        `Generated: ${data.generated_at}`;

    const cards = [
        {
            title: "Library",
            body: `
                <div class="row"><div class="label">photos total</div><div class="value">${fmtNumber(data.library.total)}</div></div>
                <div class="row"><div class="label">analyzed</div><div class="value">${fmtNumber(data.library.analyzed)}</div></div>
                <div class="row"><div class="label">pending</div><div class="value">${fmtNumber(data.library.pending)}</div></div>
                <div class="row"><div class="label">errors</div><div class="value">${fmtNumber(data.library.errors)}</div></div>
                <div class="row"><div class="label">with crop analysis</div><div class="value">${fmtNumber(data.library.with_crop_analysis)}</div></div>
            `
        },
        {
            title: "Analysis Rate",
            body: `
                <div class="row"><div class="label">rolling 10m count</div><div class="value">${fmtNumber(data.analysis.rolling_10m.count)}</div></div>
                <div class="row"><div class="label">rolling 10m ppm</div><div class="value">${fmtRate(data.analysis.rolling_10m.photos_per_minute)}</div></div>
                <div class="row"><div class="label">rolling sec/photo</div><div class="value">${fmtRate(data.analysis.rolling_10m.seconds_per_photo)}</div></div>
                <div class="row"><div class="label">session photos</div><div class="value">${fmtNumber(data.analysis.current_session.count)}</div></div>
                <div class="row"><div class="label">session ppm</div><div class="value">${fmtRate(data.analysis.current_session.photos_per_minute)}</div></div>
                <div class="row"><div class="label">session per hour</div><div class="value">${fmtRate(data.analysis.current_session.photos_per_hour)}</div></div>
                <div class="row"><div class="label">ETA minutes</div><div class="value">${fmtNumber(data.analysis.estimated_minutes_remaining)}</div></div>
                <div class="row"><div class="label">ETA hours</div><div class="value">${fmtNumber(data.analysis.estimated_hours_remaining)}</div></div>
            `
        },
        {
            title: "Ingest Jobs",
            body: `
                <div class="row"><div class="label">total</div><div class="value">${fmtNumber(data.ingest.total)}</div></div>
                <div class="row"><div class="label">pending</div><div class="value">${fmtNumber(data.ingest.pending)}</div></div>
                <div class="row"><div class="label">processing</div><div class="value">${fmtNumber(data.ingest.processing)}</div></div>
                <div class="row"><div class="label">done</div><div class="value">${fmtNumber(data.ingest.done)}</div></div>
                <div class="row"><div class="label">errors</div><div class="value">${fmtNumber(data.ingest.errors)}</div></div>
            `
        },
        {
            title: "Upload Sessions",
            body: `
                <div class="row"><div class="label">total</div><div class="value">${fmtNumber(data.uploads.total)}</div></div>
                <div class="row"><div class="label">receiving</div><div class="value">${fmtNumber(data.uploads.receiving)}</div></div>
                <div class="row"><div class="label">queued</div><div class="value">${fmtNumber(data.uploads.queued)}</div></div>
            `
        },
        {
            title: "Posts",
            body: `
                <div class="row"><div class="label">active candidates</div><div class="value">${fmtNumber(data.posts.candidate_active)}</div></div>
                <div class="row"><div class="label">all candidates</div><div class="value">${fmtNumber(data.posts.candidate_total)}</div></div>
                <div class="row"><div class="label">published</div><div class="value">${fmtNumber(data.posts.published_total)}</div></div>
            `
        },
        {
            title: "Storage: Local Root",
            body: storageRows(data.storage.local_root)
        },
        {
            title: "Storage: Curator Data",
            body: storageRows(data.storage.data_root)
        },
        {
            title: "Storage: Curator Work",
            body: storageRows(data.storage.work_root)
        },
        {
            title: "Storage: Archive Root",
            body: storageRows(data.storage.archive_host_root)
        }
    ];

    document.getElementById("cards").innerHTML =
        cards.map(card => `
            <section class="card">
                <h2>${card.title}</h2>
                ${card.body}
            </section>
        `).join("");
}

async function loadStats() {
    try {
        const response = await fetch("/api/stats/overview", {
            cache: "no-store"
        });

        if (!response.ok) {
            throw new Error(
                `HTTP ${response.status}`
            );
        }

        const data = await response.json();
        render(data);

    } catch (error) {
        document.getElementById("generatedAt").textContent =
            `Stats load failed: ${error.message}`;
    }
}

loadStats();
setInterval(loadStats, 15000);
</script>
</body>
</html>
"""


@router.get(
    "/api/stats/overview"
)
def stats_overview() -> dict:
    return build_overview()


@router.get(
    "/stats",
    response_class=HTMLResponse,
)
def stats_page() -> str:
    return STATS_PAGE
