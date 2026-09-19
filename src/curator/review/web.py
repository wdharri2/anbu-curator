# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from curator.config import load_settings
from curator.db import connect
from curator.storage.replicas import (
    human_bytes,
    report as storage_report,
)

from curator.ingest.manual import SUPPORTED_EXTENSIONS


CHUNK_SIZE = 2 * 1024 * 1024

app = FastAPI(
    title="Photo Curator",
    version="0.3.0",
)


class UploadStart(BaseModel):
    source_key: str
    filename: str
    total_bytes: int = Field(gt=0)


UPLOAD_PAGE = r"""
<!doctype html>
<html>
<head>
<meta name="viewport"
      content="width=device-width, initial-scale=1">

<title>Photo Curator</title>

<style>
body {
    font-family: system-ui, sans-serif;
    background: #111;
    color: #eee;
    max-width: 760px;
    margin: 30px auto;
    padding: 0 16px;
}

.card {
    background: #1d1d1d;
    border-radius: 14px;
    padding: 20px;
    margin-bottom: 18px;
}

button {
    font-size: 17px;
    padding: 12px 18px;
    border: 0;
    border-radius: 10px;
    margin-top: 15px;
}

input {
    margin-top: 14px;
    width: 100%;
}

.small {
    color: #aaa;
}

.file {
    padding: 12px 0;
    border-bottom: 1px solid #333;
}

.name {
    overflow-wrap: anywhere;
}

.status {
    color: #aaa;
    font-size: 14px;
    margin-top: 5px;
}

progress {
    width: 100%;
    margin-top: 7px;
}
</style>
</head>

<body>

<h1>Photo Curator</h1>

<div class="card">

<h2>Upload photos</h2>

<p class="small">
Each photo is uploaded in 1 MB resumable chunks.
If the connection drops, reselecting the same photo
will continue from the last completed chunk.
</p>

<input
    id="picker"
    type="file"
    accept="image/*,.cr3"
    multiple
>

<button id="uploadButton">
Upload selected photos
</button>

<p id="summary" class="small"></p>

</div>

<div id="files" class="card" style="display:none">
<h2>Progress</h2>
<div id="list"></div>
</div>

<script>
const CHUNK_SIZE = 2 * 1024 * 1024;

const picker = document.getElementById("picker");
const button = document.getElementById("uploadButton");
const list = document.getElementById("list");
const filesCard = document.getElementById("files");
const summary = document.getElementById("summary");


function makeRow(file, index) {
    const row = document.createElement("div");
    row.className = "file";

    const name = document.createElement("div");
    name.className = "name";
    name.textContent = `${index + 1}. ${file.name}`;

    const progress = document.createElement("progress");
    progress.max = 100;
    progress.value = 0;

    const status = document.createElement("div");
    status.className = "status";
    status.textContent = "Waiting";

    row.appendChild(name);
    row.appendChild(progress);
    row.appendChild(status);

    list.appendChild(row);

    return { progress, status };
}


async function fetchWithTimeout(url, options = {}, timeoutMs = 30000) {
    const controller = new AbortController();

    const timer = setTimeout(
        () => controller.abort(),
        timeoutMs
    );

    try {
        return await fetch(
            url,
            {
                ...options,
                signal: controller.signal
            }
        );
    } finally {
        clearTimeout(timer);
    }
}


async function getUpload(uploadId) {
    const response = await fetchWithTimeout(
        `/api/uploads/${uploadId}`,
        {},
        10000
    );

    if (!response.ok) {
        throw new Error(
            `Upload status HTTP ${response.status}`
        );
    }

    return await response.json();
}


async function startUpload(file) {
    const sourceKey = [
        file.name,
        file.size,
        file.lastModified
    ].join("|");

    const response = await fetchWithTimeout(
        "/api/uploads/start",
        {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                source_key: sourceKey,
                filename: file.name,
                total_bytes: file.size
            })
        },
        10000
    );

    if (!response.ok) {
        throw new Error(
            `Start HTTP ${response.status}`
        );
    }

    return await response.json();
}


function sendChunk(
    uploadId,
    offset,
    blob,
    fileSize,
    controls
) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();

        xhr.open(
            "PUT",
            `/api/uploads/${uploadId}/chunk?offset=${offset}`
        );

        xhr.setRequestHeader(
            "Content-Type",
            "application/octet-stream"
        );

        xhr.timeout = 20000;

        xhr.upload.onprogress = (event) => {
            if (!event.lengthComputable) {
                return;
            }

            const totalSent = offset + event.loaded;

            const percent = Math.min(
                100,
                (totalSent / fileSize) * 100
            );

            controls.progress.value = percent;

            controls.status.textContent =
                `Uploading ${percent.toFixed(1)}%`;
        };

        xhr.onload = () => {
            if (
                xhr.status >= 200
                && xhr.status < 300
            ) {
                try {
                    resolve(
                        JSON.parse(xhr.responseText)
                    );
                } catch {
                    reject(
                        new Error("Unreadable chunk response")
                    );
                }
            } else {
                reject(
                    new Error(
                        `Chunk HTTP ${xhr.status}`
                    )
                );
            }
        };

        xhr.onerror = () => {
            reject(
                new Error("Chunk connection error")
            );
        };

        xhr.ontimeout = () => {
            reject(
                new Error("Chunk timeout")
            );
        };

        xhr.send(blob);
    });
}


async function finishUpload(uploadId) {
    const response = await fetchWithTimeout(
        `/api/uploads/${uploadId}/finish`,
        {
            method: "POST"
        },
        10000
    );

    if (!response.ok) {
        throw new Error(
            `Finish HTTP ${response.status}`
        );
    }

    return await response.json();
}


async function watchJob(jobId, controls) {
    for (;;) {
        await new Promise(
            resolve => setTimeout(resolve, 1500)
        );

        try {
            const response = await fetch(
                `/api/jobs/${jobId}`
            );

            if (!response.ok) {
                continue;
            }

            const job = await response.json();

            if (job.status === "pending") {
                controls.status.textContent =
                    `Queued — job ${jobId}`;

            } else if (job.status === "processing") {
                controls.status.textContent =
                    `Processing — job ${jobId}`;

            } else if (job.status === "done") {
                controls.status.textContent =
                    `✓ Ready — Photo ID ${job.photo_id}`;
                return;

            } else if (job.status === "error") {
                controls.status.textContent =
                    `Processing error: ${job.error_message}`;
                return;
            }

        } catch {
            // Already durable after finish.
            // Keep trying when connectivity returns.
        }
    }
}


async function uploadFile(file, controls) {
    let session = await startUpload(file);

    if (session.status === "queued") {
        controls.progress.value = 100;
        controls.status.textContent =
            `Already queued — job ${session.ingest_job_id}`;

        watchJob(
            session.ingest_job_id,
            controls
        );

        return true;
    }

    let offset = session.received_bytes;

    if (offset > 0) {
        controls.progress.value =
            Math.floor((offset / file.size) * 100);

        controls.status.textContent =
            `Resuming at ${controls.progress.value}%`;
    }

    while (offset < file.size) {
        const end = Math.min(
            offset + CHUNK_SIZE,
            file.size
        );

        const blob = file.slice(offset, end);

        let sent = false;

        for (let attempt = 1; attempt <= 8; attempt++) {
            try {
                controls.status.textContent =
                    `Uploading ${Math.floor(
                        (offset / file.size) * 100
                    )}%`;

                const result = await sendChunk(
                    session.id,
                    offset,
                    blob,
                    file.size,
                    controls
                );

                offset = result.received_bytes;
                sent = true;
                break;

            } catch (error) {
                controls.status.textContent =
                    `Connection issue, retry ${attempt}/8`;

                try {
                    const current = await getUpload(
                        session.id
                    );

                    offset = current.received_bytes;

                    if (offset >= end) {
                        sent = true;
                        break;
                    }
                } catch {
                    // Retry again.
                }

                await new Promise(
                    resolve => setTimeout(
                        resolve,
                        1000 * attempt
                    )
                );
            }
        }

        if (!sent) {
            throw new Error(
                "Could not transfer current chunk"
            );
        }

        controls.progress.value =
            Math.floor((offset / file.size) * 100);
    }

    controls.progress.value = 100;
    controls.status.textContent =
        "Upload complete — queueing";

    const finished = await finishUpload(
        session.id
    );

    controls.status.textContent =
        `Queued — job ${finished.ingest_job_id}`;

    watchJob(
        finished.ingest_job_id,
        controls
    );

    return true;
}


button.onclick = async () => {
    const files = Array.from(
        picker.files
    );

    let wakeLock = null;

    try {
        if ("wakeLock" in navigator) {
            wakeLock = await navigator.wakeLock.request(
                "screen"
            );
        }
    } catch {
        // Upload still works if wake lock is unavailable.
    }

    if (!files.length) {
        summary.textContent =
            "Choose some photos first.";
        return;
    }

    const totalBytes = files.reduce(
        (sum, file) => sum + file.size,
        0
    );

    const largestFileBytes = Math.max(
        ...files.map(file => file.size)
    );

    function formatBytes(bytes) {
        const units = [
            "B",
            "KB",
            "MB",
            "GB",
            "TB"
        ];

        let value = bytes;
        let index = 0;

        while (
            value >= 1024
            && index < units.length - 1
        ) {
            value /= 1024;
            index++;
        }

        return (
            `${value.toFixed(1)} ${units[index]}`
        );
    }

    button.disabled = true;
    picker.disabled = true;

    summary.textContent =
        `Checking storage for ` +
        `${files.length} photos ` +
        `(${formatBytes(totalBytes)})...`;

    let preflight;

    try {
        const response = await fetch(
            "/api/storage/preflight"
            + `?total_bytes=${totalBytes}`
            + `&largest_file_bytes=${largestFileBytes}`
            + `&file_count=${files.length}`
        );

        if (!response.ok) {
            throw new Error(
                await response.text()
            );
        }

        preflight = await response.json();

    } catch (error) {
        summary.textContent =
            "Storage safety check failed. " +
            "Upload was not started.";

        button.disabled = false;
        picker.disabled = false;

        if (wakeLock) {
            try {
                await wakeLock.release();
            } catch {
                // Ignore release failures.
            }
        }

        return;
    }

    if (!preflight.safe) {
        const reason = (
            preflight.reasons.length
            ? preflight.reasons.join(" ")
            : "Insufficient safe storage."
        );

        summary.textContent =
            `Upload blocked. ${reason} ` +
            `Selected ${preflight.display.selected}; ` +
            `safe batch allowance about ` +
            `${preflight.display.maximum_batch}.`;

        button.disabled = false;
        picker.disabled = false;

        if (wakeLock) {
            try {
                await wakeLock.release();
            } catch {
                // Ignore release failures.
            }
        }

        return;
    }

    list.innerHTML = "";
    filesCard.style.display = "block";

    const rows = files.map(makeRow);

    summary.textContent =
        `Storage OK. Selected ` +
        `${preflight.display.selected}; ` +
        `Local safe headroom ` +
        `${preflight.display.local_headroom}; ` +
        `Archive safe headroom ` +
        `${preflight.display.archive_headroom}.`;

    let queued = 0;
    let failed = 0;
    let nextIndex = 0;
    let completed = 0;

    const CONCURRENCY = 3;

    async function worker() {
        for (;;) {
            const i = nextIndex++;

            if (i >= files.length) {
                return;
            }

            try {
                await uploadFile(
                    files[i],
                    rows[i]
                );

                queued++;

            } catch (error) {
                rows[i].status.textContent =
                    `Stopped: ${error.message}`;

                failed++;
            }

            completed++;

            summary.textContent =
                `${completed}/${files.length} sent — ` +
                `${queued} queued, ${failed} failed`;
        }
    }

    summary.textContent =
        `Uploading ${files.length} photos ` +
        `(${preflight.display.selected}) with ` +
        `${Math.min(CONCURRENCY, files.length)} ` +
        `parallel transfers. ` +
        `Safe batch allowance: ` +
        `${preflight.display.maximum_batch}.`;

    await Promise.all(
        Array.from(
            {
                length: Math.min(
                    CONCURRENCY,
                    files.length
                )
            },
            () => worker()
        )
    );

    summary.textContent =
        `${queued} queued, ${failed} failed.`;

    button.disabled = false;
    picker.disabled = false;

    if (wakeLock) {
        try {
            await wakeLock.release();
        } catch {
            // Ignore release failures.
        }
    }
};
</script>

</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
@app.get("/upload", response_class=HTMLResponse)
def upload_page() -> str:
    return UPLOAD_PAGE


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "photo-curator",
        "version": "0.3.0",
        "upload_mode": "resumable_chunks",
        "chunk_bytes": CHUNK_SIZE,
    }



@app.get("/api/storage/preflight")
def storage_preflight(
    total_bytes: int = 0,
    largest_file_bytes: int = 0,
    file_count: int = 0,
) -> dict:
    if (
        total_bytes < 0
        or largest_file_bytes < 0
        or file_count < 0
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid storage preflight values",
        )

    storage = storage_report()

    local_headroom = storage[
        "headroom_to_80_percent"
    ][
        "local"
    ]

    archive_headroom = storage[
        "headroom_to_80_percent"
    ][
        "archive"
    ]

    # Permanent new usage on each durable store is
    # approximately the selected batch size.
    #
    # Local temporarily needs an extra copy of the
    # file currently moving from staging into its
    # permanent working location, so reserve enough
    # room for the largest selected file too.
    required_local = (
        total_bytes
        + largest_file_bytes
    )

    required_archive = total_bytes

    protection_ok = (
        storage["underprotected_photos"] == 0
        and storage["zero_durable_photos"] == 0
    )

    local_ok = (
        required_local
        <= local_headroom
    )

    archive_ok = (
        required_archive
        <= archive_headroom
    )

    safe = (
        protection_ok
        and local_ok
        and archive_ok
    )

    # Approximate maximum batch size for this
    # particular largest-file requirement.
    max_for_local = max(
        0,
        local_headroom
        - largest_file_bytes,
    )

    maximum_batch = min(
        max_for_local,
        archive_headroom,
    )

    reasons = []

    if not protection_ok:
        reasons.append(
            "Existing library is below the "
            "minimum durable-replica policy."
        )

    if not local_ok:
        reasons.append(
            "Local does not have enough safe "
            "headroom for the selected batch plus "
            "temporary ingest duplication."
        )

    if not archive_ok:
        reasons.append(
            "Archive does not have enough safe "
            "archive headroom for the selected batch."
        )

    return {
        "safe": safe,
        "file_count": file_count,
        "selected_bytes": total_bytes,
        "largest_file_bytes": largest_file_bytes,

        "required": {
            "local_bytes": required_local,
            "archive_bytes": required_archive,
        },

        "headroom_to_80_percent": {
            "local_bytes": local_headroom,
            "archive_bytes": archive_headroom,
        },

        "maximum_batch_bytes": maximum_batch,

        "display": {
            "selected": human_bytes(
                total_bytes
            ),
            "largest_file": human_bytes(
                largest_file_bytes
            ),
            "local_required": human_bytes(
                required_local
            ),
            "local_headroom": human_bytes(
                local_headroom
            ),
            "archive_required": human_bytes(
                required_archive
            ),
            "archive_headroom": human_bytes(
                archive_headroom
            ),
            "maximum_batch": human_bytes(
                maximum_batch
            ),
        },

        "protection": {
            "minimum_durable_replicas": storage[
                "minimum_durable_original_replicas"
            ],
            "protected_photos": storage[
                "protected_photos"
            ],
            "underprotected_photos": storage[
                "underprotected_photos"
            ],
            "zero_durable_photos": storage[
                "zero_durable_photos"
            ],
        },

        "reasons": reasons,
    }


@app.post("/api/uploads/start")
def start_upload(request: UploadStart) -> dict:
    settings = load_settings()

    original_name = Path(
        request.filename
    ).name

    suffix = Path(
        original_name
    ).suffix.lower()

    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported extension: {suffix}",
        )

    session_dir = (
        settings.incoming / "_sessions"
    )
    session_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    with connect() as db:
        existing = db.execute(
            """
            SELECT *
            FROM upload_sessions
            WHERE source_key = ?
            """,
            (request.source_key,),
        ).fetchone()

        if existing:
            row = dict(existing)

            if (
                row["total_bytes"]
                != request.total_bytes
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Upload identity collision",
                )

            path = Path(
                row["staging_path"]
            )

            if (
                row["status"] == "receiving"
                and not path.exists()
            ):
                path.touch()

                db.execute(
                    """
                    UPDATE upload_sessions
                    SET received_bytes = 0,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (row["id"],),
                )
                db.commit()

                row["received_bytes"] = 0

            return row

        upload_id = uuid.uuid4().hex

        staging_path = (
            session_dir /
            f"{upload_id}{suffix}"
        )

        staging_path.touch()

        db.execute(
            """
            INSERT INTO upload_sessions (
                id,
                source_key,
                original_filename,
                total_bytes,
                received_bytes,
                staging_path,
                status
            )
            VALUES (?, ?, ?, ?, 0, ?, 'receiving')
            """,
            (
                upload_id,
                request.source_key,
                original_name,
                request.total_bytes,
                str(staging_path),
            ),
        )

        db.commit()

    return {
        "id": upload_id,
        "original_filename": original_name,
        "total_bytes": request.total_bytes,
        "received_bytes": 0,
        "status": "receiving",
        "ingest_job_id": None,
    }


@app.get("/api/uploads/{upload_id}")
def upload_status(upload_id: str) -> dict:
    with connect() as db:
        row = db.execute(
            """
            SELECT *
            FROM upload_sessions
            WHERE id = ?
            """,
            (upload_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Upload not found",
        )

    return dict(row)


@app.put("/api/uploads/{upload_id}/chunk")
async def upload_chunk(
    upload_id: str,
    request: Request,
    offset: int,
) -> dict:
    with connect() as db:
        row = db.execute(
            """
            SELECT *
            FROM upload_sessions
            WHERE id = ?
            """,
            (upload_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Upload not found",
        )

    row = dict(row)

    if row["status"] != "receiving":
        raise HTTPException(
            status_code=409,
            detail="Upload is not receiving",
        )

    current = row["received_bytes"]

    if offset != current:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Wrong offset",
                "expected_offset": current,
            },
        )

    data = await request.body()

    if not data:
        raise HTTPException(
            status_code=400,
            detail="Empty chunk",
        )

    if len(data) > CHUNK_SIZE:
        raise HTTPException(
            status_code=413,
            detail="Chunk too large",
        )

    if current + len(data) > row["total_bytes"]:
        raise HTTPException(
            status_code=400,
            detail="Chunk exceeds expected file size",
        )

    path = Path(
        row["staging_path"]
    )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open("r+b") as output:
        output.seek(current)
        output.write(data)
        output.flush()
        os.fsync(output.fileno())

    new_total = current + len(data)

    with connect() as db:
        db.execute(
            """
            UPDATE upload_sessions
            SET received_bytes = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                new_total,
                upload_id,
            ),
        )

        db.commit()

    return {
        "id": upload_id,
        "received_bytes": new_total,
        "total_bytes": row["total_bytes"],
    }


@app.post("/api/uploads/{upload_id}/finish")
def finish_upload(upload_id: str) -> dict:
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")

        row = db.execute(
            """
            SELECT *
            FROM upload_sessions
            WHERE id = ?
            """,
            (upload_id,),
        ).fetchone()

        if row is None:
            db.rollback()

            raise HTTPException(
                status_code=404,
                detail="Upload not found",
            )

        row = dict(row)

        if row["status"] == "queued":
            db.commit()

            return {
                "status": "queued",
                "ingest_job_id":
                    row["ingest_job_id"],
            }

        if (
            row["received_bytes"]
            != row["total_bytes"]
        ):
            db.rollback()

            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Upload incomplete",
                    "received_bytes":
                        row["received_bytes"],
                    "total_bytes":
                        row["total_bytes"],
                },
            )

        cursor = db.execute(
            """
            INSERT INTO ingest_jobs (
                original_filename,
                staging_path,
                source,
                status
            )
            VALUES (?, ?, 'web_upload', 'pending')
            """,
            (
                row["original_filename"],
                row["staging_path"],
            ),
        )

        job_id = cursor.lastrowid

        db.execute(
            """
            UPDATE upload_sessions
            SET status = 'queued',
                ingest_job_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                job_id,
                upload_id,
            ),
        )

        db.commit()

    return {
        "status": "queued",
        "ingest_job_id": job_id,
    }


@app.get("/api/jobs/{job_id}")
def job_status(job_id: int) -> dict:
    with connect() as db:
        row = db.execute(
            """
            SELECT
                id,
                original_filename,
                status,
                photo_id,
                result_message,
                error_message,
                created_at,
                updated_at
            FROM ingest_jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Job not found",
        )

    return dict(row)


# Curated-post review UI
from curator.review.posts_web import router as posts_router
from curator.stats.web import router as stats_router
app.include_router(posts_router)
app.include_router(stats_router)
