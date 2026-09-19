# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import sqlite3
from pathlib import Path

from curator.config import load_settings


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS photos (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    sha256              TEXT NOT NULL UNIQUE,
    filename            TEXT NOT NULL,
    source              TEXT NOT NULL DEFAULT 'manual',

    archive_path        TEXT,
    work_path           TEXT,

    captured_at         TEXT,
    latitude            REAL,
    longitude           REAL,
    width               INTEGER,
    height              INTEGER,

    exif_json           TEXT,

    analysis_status     TEXT NOT NULL DEFAULT 'pending',
    analysis_json       TEXT,
    scores_json         TEXT,

    analysis_model      TEXT,
    analysis_version    INTEGER,
    prompt_version      INTEGER,

    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);


CREATE TABLE IF NOT EXISTS operations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    label               TEXT,
    description         TEXT,

    started_at          TEXT,
    ended_at            TEXT,

    grouping_version    INTEGER,

    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);


CREATE TABLE IF NOT EXISTS operation_photos (
    operation_id        INTEGER NOT NULL,
    photo_id            INTEGER NOT NULL,

    similarity_score    REAL,

    decision            TEXT,
    decision_reason     TEXT,

    operation_rank      INTEGER,

    PRIMARY KEY (operation_id, photo_id),

    FOREIGN KEY (operation_id)
        REFERENCES operations(id)
        ON DELETE CASCADE,

    FOREIGN KEY (photo_id)
        REFERENCES photos(id)
        ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS post_candidates (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    title               TEXT,
    post_type           TEXT,

    status              TEXT NOT NULL DEFAULT 'candidate',

    story_summary       TEXT,
    curator_reasoning   TEXT,

    scores_json         TEXT,

    caption_draft       TEXT,

    curator_model       TEXT,
    curator_version     INTEGER,
    prompt_version      INTEGER,

    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);


CREATE TABLE IF NOT EXISTS post_candidate_photos (
    post_id             INTEGER NOT NULL,
    photo_id            INTEGER NOT NULL,

    position            INTEGER NOT NULL,

    is_cover            INTEGER NOT NULL DEFAULT 0,

    role                TEXT,
    inclusion_reason    TEXT,

    PRIMARY KEY (post_id, photo_id),

    FOREIGN KEY (post_id)
        REFERENCES post_candidates(id)
        ON DELETE CASCADE,

    FOREIGN KEY (photo_id)
        REFERENCES photos(id)
        ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS published_posts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    candidate_id        INTEGER,

    posted_at           TEXT,

    caption_final       TEXT,
    notes               TEXT,

    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (candidate_id)
        REFERENCES post_candidates(id)
        ON DELETE SET NULL
);


CREATE TABLE IF NOT EXISTS published_post_photos (
    published_post_id   INTEGER NOT NULL,
    photo_id            INTEGER NOT NULL,

    position            INTEGER NOT NULL,
    is_cover            INTEGER NOT NULL DEFAULT 0,

    PRIMARY KEY (published_post_id, photo_id),

    FOREIGN KEY (published_post_id)
        REFERENCES published_posts(id)
        ON DELETE CASCADE,

    FOREIGN KEY (photo_id)
        REFERENCES photos(id)
        ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS ai_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    stage               TEXT NOT NULL,

    model               TEXT,
    prompt_version      INTEGER,
    pipeline_version    INTEGER,

    input_fingerprint   TEXT,

    output_json         TEXT,

    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);


CREATE INDEX IF NOT EXISTS idx_photos_captured_at
    ON photos(captured_at);

CREATE INDEX IF NOT EXISTS idx_photos_analysis_status
    ON photos(analysis_status);

CREATE INDEX IF NOT EXISTS idx_operation_photos_photo
    ON operation_photos(photo_id);

CREATE INDEX IF NOT EXISTS idx_post_candidate_status
    ON post_candidates(status);

CREATE INDEX IF NOT EXISTS idx_published_posts_posted_at
    ON published_posts(posted_at);

CREATE INDEX IF NOT EXISTS idx_ai_runs_stage
    ON ai_runs(stage);
"""



INGEST_JOB_SCHEMA = """
CREATE TABLE IF NOT EXISTS ingest_jobs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    original_filename   TEXT NOT NULL,
    staging_path        TEXT NOT NULL,
    source              TEXT NOT NULL DEFAULT 'web_upload',

    status              TEXT NOT NULL DEFAULT 'pending',

    photo_id            INTEGER,
    result_message      TEXT,
    error_message       TEXT,

    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (photo_id)
        REFERENCES photos(id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_ingest_jobs_status
    ON ingest_jobs(status);

CREATE INDEX IF NOT EXISTS idx_ingest_jobs_created
    ON ingest_jobs(created_at);
"""


UPLOAD_SESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS upload_sessions (
    id                  TEXT PRIMARY KEY,
    source_key          TEXT NOT NULL UNIQUE,
    original_filename   TEXT NOT NULL,

    total_bytes         INTEGER NOT NULL,
    received_bytes      INTEGER NOT NULL DEFAULT 0,

    staging_path        TEXT NOT NULL,

    status              TEXT NOT NULL DEFAULT 'receiving',
    ingest_job_id       INTEGER,

    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (ingest_job_id)
        REFERENCES ingest_jobs(id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_upload_sessions_status
    ON upload_sessions(status);
"""

def connect(db_path: Path | None = None) -> sqlite3.Connection:
    if db_path is None:
        db_path = load_settings().db_path

    db_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    return connection



def _table_columns(
    connection: sqlite3.Connection,
    table: str,
) -> set[str]:
    return {
        row["name"]
        for row in connection.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    }


def run_migrations(
    connection: sqlite3.Connection,
) -> None:
    photo_columns = _table_columns(
        connection,
        "photos",
    )

    if "curation_state" not in photo_columns:
        connection.execute(
            """
            ALTER TABLE photos
            ADD COLUMN curation_state TEXT
            NOT NULL DEFAULT 'eligible'
            """
        )

    if (
        "curation_state_updated_at"
        not in photo_columns
    ):
        connection.execute(
            """
            ALTER TABLE photos
            ADD COLUMN curation_state_updated_at TEXT
            """
        )

    candidate_columns = _table_columns(
        connection,
        "post_candidates",
    )

    if "editorial_feedback" not in candidate_columns:
        connection.execute(
            """
            ALTER TABLE post_candidates
            ADD COLUMN editorial_feedback TEXT
            """
        )

    if (
        "superseded_by_candidate_id"
        not in candidate_columns
    ):
        connection.execute(
            """
            ALTER TABLE post_candidates
            ADD COLUMN superseded_by_candidate_id INTEGER
            """
        )

    candidate_photo_columns = _table_columns(
        connection,
        "post_candidate_photos",
    )

    if (
        "review_disposition"
        not in candidate_photo_columns
    ):
        connection.execute(
            """
            ALTER TABLE post_candidate_photos
            ADD COLUMN review_disposition TEXT
            NOT NULL DEFAULT 'use'
            """
        )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_photos_curation_state
        ON photos(curation_state)
        """
    )

def initialize_database() -> Path:
    settings = load_settings()

    with connect(settings.db_path) as connection:
        connection.executescript(
            SCHEMA
            + INGEST_JOB_SCHEMA
            + UPLOAD_SESSION_SCHEMA
        )
        run_migrations(connection)
        connection.commit()

    return settings.db_path
