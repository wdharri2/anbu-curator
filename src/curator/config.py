# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    environment: str

    data_root: Path
    db_path: Path

    work_root: Path
    incoming: Path
    cache: Path
    thumbnails: Path
    posts: Path

    archive_host: str
    archive_user: str
    archive_root: str

    @property
    def archive_target(self) -> str:
        return f"{self.archive_user}@{self.archive_host}"

    @property
    def archive_uri(self) -> str:
        return f"{self.archive_target}:{self.archive_root}"


def load_settings(env_file: str | Path = ".env") -> Settings:
    load_dotenv(env_file)

    required = [
        "CURATOR_ENV",
        "CURATOR_DATA_ROOT",
        "CURATOR_DB",
        "CURATOR_WORK_ROOT",
        "CURATOR_INCOMING",
        "CURATOR_CACHE",
        "CURATOR_THUMBNAILS",
        "CURATOR_POSTS",
        "CURATOR_ARCHIVE_HOST",
        "CURATOR_ARCHIVE_USER",
        "CURATOR_ARCHIVE_ROOT",
    ]

    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: "
            + ", ".join(missing)
        )

    return Settings(
        environment=os.environ["CURATOR_ENV"],
        data_root=Path(os.environ["CURATOR_DATA_ROOT"]),
        db_path=Path(os.environ["CURATOR_DB"]),
        work_root=Path(os.environ["CURATOR_WORK_ROOT"]),
        incoming=Path(os.environ["CURATOR_INCOMING"]),
        cache=Path(os.environ["CURATOR_CACHE"]),
        thumbnails=Path(os.environ["CURATOR_THUMBNAILS"]),
        posts=Path(os.environ["CURATOR_POSTS"]),
        archive_host=os.environ["CURATOR_ARCHIVE_HOST"],
        archive_user=os.environ["CURATOR_ARCHIVE_USER"],
        archive_root=os.environ["CURATOR_ARCHIVE_ROOT"],
    )


def validate_local_storage(settings: Settings) -> dict[str, bool]:
    paths = {
        "data_root": settings.data_root,
        "db_parent": settings.db_path.parent,
        "work_root": settings.work_root,
        "incoming": settings.incoming,
        "cache": settings.cache,
        "thumbnails": settings.thumbnails,
        "posts": settings.posts,
    }

    return {
        name: path.exists() and path.is_dir()
        for name, path in paths.items()
    }


def validate_archive(settings: Settings) -> tuple[bool, str]:
    command = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5",
        settings.archive_target,
        f'test -d "{settings.archive_root}" && printf ARCHIVE_OK',
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    ok = result.returncode == 0 and result.stdout.strip() == "ARCHIVE_OK"

    message = (
        result.stdout.strip()
        if ok
        else (result.stderr.strip() or "Archive validation failed")
    )

    return ok, message
