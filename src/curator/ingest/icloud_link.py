# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import re
import shlex
import subprocess
import time
import uuid

from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from curator.config import load_settings
from curator.db import connect
from curator.storage.lifecycle import (
    archive_config,
    run_ssh,
)
from curator.storage.replicas import (
    human_bytes,
    report as storage_report,
)


PHOTO_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".heic",
    ".heif",
    ".webp",
    ".cr3",
}

SUCCESS_JOB_STATES = {
    "done",
    "complete",
    "completed",
    "ready",
}

FAIL_JOB_STATES = {
    "error",
    "failed",
}


def validate_url(value: str) -> str:
    value = value.strip()

    parsed = urlparse(value)

    if parsed.scheme != "https":
        raise ValueError(
            "iCloud Link must use HTTPS"
        )

    host = parsed.hostname or ""

    allowed_hosts = {
        "share.icloud.com",
        "www.icloud.com",
        "icloud.com",
    }

    if host not in allowed_hosts:
        raise ValueError(
            "Expected an iCloud Photos link"
        )

    valid_path = (
        parsed.path.startswith("/photos/")
        or parsed.path == "/photos/"
        or parsed.path == "/photos"
    )

    if not valid_path:
        raise ValueError(
            "This importer expects a normal "
            "iCloud Photos link, such as "
            "share.icloud.com/photos/..."
        )

    return value


def link_id(url: str) -> str:
    return hashlib.sha256(
        url.encode("utf-8")
    ).hexdigest()[:20]


def chromium_path() -> str:
    candidates = [
        Path("/usr/bin/chromium"),
        Path("/usr/bin/chromium-browser"),
    ]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    raise RuntimeError(
        "System Chromium not found"
    )


def dismiss_overlays(page) -> None:
    patterns = [
        re.compile(r"accept", re.I),
        re.compile(r"agree", re.I),
        re.compile(r"continue", re.I),
    ]

    for pattern in patterns:
        try:
            locator = page.get_by_role(
                "button",
                name=pattern,
            )

            count = min(
                locator.count(),
                5,
            )

            for index in range(count):
                button = locator.nth(index)

                if button.is_visible():
                    try:
                        button.click(
                            timeout=1500
                        )
                        page.wait_for_timeout(
                            500
                        )
                    except Exception:
                        pass

        except Exception:
            pass


def visible_download_controls(
    page,
) -> list:
    selectors = [
        'button[aria-label*="download" i]',
        'button[title*="download" i]',
        'a[aria-label*="download" i]',
        'a[title*="download" i]',
        'button:has-text("Download")',
        'a:has-text("Download")',
    ]

    results = []
    seen = set()

    for selector in selectors:
        try:
            locator = page.locator(
                selector
            )

            count = min(
                locator.count(),
                20,
            )

            for index in range(count):
                item = locator.nth(index)

                try:
                    if not item.is_visible():
                        continue

                    identity = (
                        item.get_attribute("aria-label"),
                        item.get_attribute("title"),
                        item.inner_text(
                            timeout=1000
                        ),
                    )

                    if identity in seen:
                        continue

                    seen.add(identity)
                    results.append(item)

                except Exception:
                    continue

        except Exception:
            continue

    return results


def control_label(locator) -> str:
    parts = []

    for attribute in (
        "aria-label",
        "title",
    ):
        try:
            value = locator.get_attribute(
                attribute
            )

            if value:
                parts.append(value.strip())
        except Exception:
            pass

    try:
        value = locator.inner_text(
            timeout=1000
        ).strip()

        if value:
            parts.append(value)
    except Exception:
        pass

    label = " / ".join(
        dict.fromkeys(parts)
    )

    return label or "Download control"


def infer_item_count(page) -> int | None:
    try:
        text = page.locator(
            "body"
        ).inner_text(
            timeout=3000
        )
    except Exception:
        return None

    patterns = [
        r"\b([\d,]+)\s+photos?\b",
        r"\b([\d,]+)\s+items?\b",
        r"\b([\d,]+)\s+photos?\s+and\s+videos?\b",
    ]

    values = []

    for pattern in patterns:
        for match in re.findall(
            pattern,
            text,
            flags=re.I,
        ):
            try:
                values.append(
                    int(
                        match.replace(
                            ",",
                            "",
                        )
                    )
                )
            except ValueError:
                pass

    if not values:
        return None

    return max(values)


def open_collection(
    url: str,
):
    playwright = sync_playwright().start()

    browser = playwright.chromium.launch(
        executable_path=chromium_path(),
        headless=True,
        args=[
            "--disable-dev-shm-usage",
        ],
    )

    context = browser.new_context(
        accept_downloads=True,
        viewport={
            "width": 1600,
            "height": 1000,
        },
    )

    page = context.new_page()

    page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=120_000,
    )

    try:
        page.wait_for_load_state(
            "networkidle",
            timeout=20_000,
        )
    except PlaywrightTimeoutError:
        pass

    page.wait_for_timeout(
        5000
    )

    dismiss_overlays(page)

    return (
        playwright,
        browser,
        context,
        page,
    )


def probe_link(
    url: str,
) -> dict:
    (
        playwright,
        browser,
        context,
        page,
    ) = open_collection(url)

    try:
        controls = visible_download_controls(
            page
        )

        return {
            "link_id": link_id(url),
            "accessible": True,
            "download_controls": [
                control_label(item)
                for item in controls[:10]
            ],
            "claimed_item_count":
                infer_item_count(page),
        }

    finally:
        browser.close()
        playwright.stop()


def capture_bulk_download(
    url: str,
) -> dict:
    (
        playwright,
        browser,
        context,
        page,
    ) = open_collection(url)

    try:
        controls = visible_download_controls(
            page
        )

        if not controls:
            raise RuntimeError(
                "No visible Download control "
                "was found on the iCloud page"
            )

        last_error = None

        # A collection may reveal another
        # Download button after the first click,
        # so allow a few rounds.
        for round_number in range(1, 4):
            controls = visible_download_controls(
                page
            )

            if not controls:
                break

            for control in controls:
                label = control_label(
                    control
                )

                print(
                    "trying_download_control="
                    + label,
                    flush=True,
                )

                try:
                    with page.expect_download(
                        timeout=20_000
                    ) as info:
                        control.click(
                            timeout=5000
                        )

                    download = info.value

                    signed_url = download.url
                    filename = (
                        download.suggested_filename
                        or "icloud-download.zip"
                    )

                    cookies = context.cookies(
                        [signed_url]
                    )

                    cookie_header = "; ".join(
                        f"{cookie['name']}="
                        f"{cookie['value']}"
                        for cookie in cookies
                    )

                    # Prevent Chromium from keeping
                    # a second giant copy on Local.
                    try:
                        download.cancel()
                    except Exception:
                        pass

                    return {
                        "url": signed_url,
                        "filename": filename,
                        "cookie_header":
                            cookie_header,
                    }

                except PlaywrightTimeoutError as exc:
                    last_error = exc

                    # Clicking may have opened a
                    # secondary download dialog.
                    page.wait_for_timeout(
                        1500
                    )

                except Exception as exc:
                    last_error = exc

            page.wait_for_timeout(
                1000
            )

        raise RuntimeError(
            "iCloud page did not start a bulk "
            "download"
        ) from last_error

    finally:
        browser.close()
        playwright.stop()


def safe_filename(
    name: str,
) -> str:
    name = Path(name).name

    cleaned = re.sub(
        r"[^A-Za-z0-9._ -]+",
        "_",
        name,
    ).strip()

    return (
        cleaned
        or "icloud-download.zip"
    )


def remote_import_root(
    url: str,
) -> str:
    _, _, archive_root = archive_config()

    base = (
        Path(archive_root).parent
        / "icloud-imports"
        / link_id(url)
    )

    return str(base)


def remote_exists(
    path: str,
) -> bool:
    result = run_ssh(
        "test -f "
        + shlex.quote(path)
        + " && echo YES || echo NO"
    )

    return (
        result.stdout.strip()
        == "YES"
    )


def download_to_archive(
    *,
    url: str,
    remote_root: str,
    download_info: dict,
) -> str:
    filename = safe_filename(
        download_info["filename"]
    )

    remote_path = str(
        Path(remote_root)
        / filename
    )

    run_ssh(
        "mkdir -p "
        + shlex.quote(
            remote_root
        )
    )

    if remote_exists(remote_path):
        print(
            "remote_bulk_download="
            "REUSING_EXISTING"
        )

        return remote_path

    temp_path = (
        remote_path
        + ".part"
    )

    cookie_header = download_info[
        "cookie_header"
    ]

    command = [
        "curl",
        "-fL",
        "--retry",
        "8",
        "--retry-delay",
        "3",
        "--connect-timeout",
        "30",
        "--output",
        temp_path,
    ]

    if cookie_header:
        command.extend(
            [
                "-H",
                "Cookie: "
                + cookie_header,
            ]
        )

    command.append(
        download_info["url"]
    )

    shell = " ".join(
        shlex.quote(part)
        for part in command
    )

    shell += (
        " && mv -- "
        + shlex.quote(temp_path)
        + " "
        + shlex.quote(remote_path)
    )

    print(
        "remote_bulk_download=STARTING"
    )

    # Signed CDN URLs and cookies are
    # intentionally never printed.
    run_ssh(shell)

    print(
        "remote_bulk_download=COMPLETE"
    )

    return remote_path


def remote_magic(
    path: str,
) -> str:
    command = (
        "python3 -c "
        + shlex.quote(
            "import sys; "
            "p=sys.argv[1]; "
            "print(open(p,'rb').read(4).hex())"
        )
        + " "
        + shlex.quote(path)
    )

    result = run_ssh(
        command
    )

    return result.stdout.strip()


def extract_remote(
    *,
    remote_file: str,
    remote_root: str,
) -> str:
    extracted = str(
        Path(remote_root)
        / "extracted"
    )

    run_ssh(
        "mkdir -p "
        + shlex.quote(extracted)
    )

    magic = remote_magic(
        remote_file
    )

    if magic.startswith("504b"):
        marker = str(
            Path(extracted)
            / ".extract-complete"
        )

        marker_exists = run_ssh(
            "test -f "
            + shlex.quote(marker)
            + " && echo YES || echo NO"
        ).stdout.strip()

        if marker_exists == "YES":
            print(
                "remote_extract="
                "REUSING_EXISTING"
            )

            return extracted

        print(
            "remote_extract=STARTING"
        )

        command = (
            "python3 -m zipfile -e "
            + shlex.quote(remote_file)
            + " "
            + shlex.quote(extracted)
            + " && touch "
            + shlex.quote(marker)
        )

        run_ssh(command)

        print(
            "remote_extract=COMPLETE"
        )

    else:
        # Single-asset links are supported too.
        target = str(
            Path(extracted)
            / Path(remote_file).name
        )

        run_ssh(
            "cp -n -- "
            + shlex.quote(remote_file)
            + " "
            + shlex.quote(target)
        )

    return extracted


def remote_manifest(
    remote_directory: str,
) -> list[dict]:
    script = r'''
import hashlib
import json
import os
import sys

root = os.path.abspath(sys.argv[1])

for current, dirs, files in os.walk(root):
    dirs.sort()
    files.sort()

    for filename in files:
        if filename == ".extract-complete":
            continue

        path = os.path.join(
            current,
            filename,
        )

        h = hashlib.sha256()

        with open(path, "rb") as handle:
            for chunk in iter(
                lambda: handle.read(1024 * 1024),
                b"",
            ):
                h.update(chunk)

        print(
            json.dumps(
                {
                    "path": path,
                    "name": filename,
                    "bytes": os.path.getsize(
                        path
                    ),
                    "sha256": h.hexdigest(),
                },
                ensure_ascii=False,
            )
        )
'''

    command = (
        "python3 -c "
        + shlex.quote(script)
        + " "
        + shlex.quote(
            remote_directory
        )
    )

    result = run_ssh(
        command
    )

    items = []

    for line in result.stdout.splitlines():
        line = line.strip()

        if not line:
            continue

        items.append(
            json.loads(line)
        )

    return items


def photo_exists(
    sha256: str,
) -> int | None:
    with connect() as db:
        row = db.execute(
            """
            SELECT id
            FROM photos
            WHERE sha256 = ?
            """,
            (sha256,),
        ).fetchone()

    if row is None:
        return None

    return int(row["id"])


def preflight_manifest(
    items: list[dict],
) -> dict:
    supported = []
    unsupported = []
    duplicates = []

    for item in items:
        suffix = Path(
            item["name"]
        ).suffix.lower()

        if suffix not in PHOTO_EXTENSIONS:
            unsupported.append(item)
            continue

        existing = photo_exists(
            item["sha256"]
        )

        if existing is not None:
            duplicate = dict(item)
            duplicate[
                "existing_photo_id"
            ] = existing

            duplicates.append(
                duplicate
            )
            continue

        supported.append(item)

    total_bytes = sum(
        item["bytes"]
        for item in supported
    )

    largest_file = max(
        (
            item["bytes"]
            for item in supported
        ),
        default=0,
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

    # Same safety policy as web upload.
    #
    # Local needs the permanent batch plus
    # temporary room for one file in staging.
    local_required = (
        total_bytes
        + largest_file
    )

    # The iCloud extraction itself already exists
    # on Archive and is reflected in current df
    # usage. This is the additional permanent
    # content-addressed archive space required.
    archive_required = total_bytes

    protection_ok = (
        storage["underprotected_photos"]
        == 0
        and storage["zero_durable_photos"]
        == 0
    )

    safe = (
        protection_ok
        and local_required
        <= local_headroom
        and archive_required
        <= archive_headroom
    )

    return {
        "safe": safe,
        "supported": supported,
        "unsupported": unsupported,
        "duplicates": duplicates,
        "new_files": len(supported),
        "new_bytes": total_bytes,
        "largest_file": largest_file,
        "local_required":
            local_required,
        "archive_required":
            archive_required,
        "local_headroom":
            local_headroom,
        "archive_headroom":
            archive_headroom,
    }


def local_sha256(
    path: Path,
) -> str:
    h = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(
                1024 * 1024
            ),
            b"",
        ):
            h.update(chunk)

    return h.hexdigest()


def queue_ingest(
    *,
    staging_path: Path,
    original_filename: str,
) -> int:
    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO ingest_jobs (
                original_filename,
                staging_path,
                source,
                status
            )
            VALUES (?, ?, 'icloud_link', 'pending')
            """,
            (
                original_filename,
                str(staging_path),
            ),
        )

        db.commit()

        return int(
            cursor.lastrowid
        )


def job_state(
    job_id: int,
) -> dict:
    with connect() as db:
        row = db.execute(
            """
            SELECT
                id,
                status,
                photo_id,
                result_message,
                error_message
            FROM ingest_jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()

    if row is None:
        raise RuntimeError(
            f"Job {job_id} disappeared"
        )

    return dict(row)


def wait_for_job(
    job_id: int,
    *,
    timeout_seconds: int = 1800,
) -> dict:
    deadline = (
        time.monotonic()
        + timeout_seconds
    )

    while True:
        state = job_state(
            job_id
        )

        status = str(
            state["status"]
        ).lower()

        if status in SUCCESS_JOB_STATES:
            return state

        if status in FAIL_JOB_STATES:
            raise RuntimeError(
                "Ingest job failed: "
                + str(
                    state[
                        "error_message"
                    ]
                )
            )

        if (
            time.monotonic()
            >= deadline
        ):
            raise TimeoutError(
                f"Ingest job {job_id} "
                "did not finish in time"
            )

        time.sleep(2)


def rsync_from_archive(
    *,
    remote_path: str,
    local_path: Path,
) -> None:
    host, user, _ = archive_config()

    local_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    command = [
        "rsync",
        "-a",
        "--partial",
        "--protect-args",
        f"{user}@{host}:"
        f"{remote_path}",
        str(local_path),
    ]

    subprocess.run(
        command,
        check=True,
    )


def import_items(
    *,
    url: str,
    items: list[dict],
) -> dict:
    settings = load_settings()

    staging_root = (
        settings.incoming
        / "_icloud_link"
        / link_id(url)
    )

    staging_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    imported = 0
    skipped = 0
    failures = []

    total = len(items)

    for index, item in enumerate(
        items,
        start=1,
    ):
        existing = photo_exists(
            item["sha256"]
        )

        if existing is not None:
            skipped += 1

            print(
                f"[{index}/{total}] "
                f"duplicate "
                f"photo={existing} "
                f"name={item['name']!r}",
                flush=True,
            )

            continue

        suffix = Path(
            item["name"]
        ).suffix.lower()

        staging_path = (
            staging_root
            / (
                uuid.uuid4().hex
                + suffix
            )
        )

        print(
            f"[{index}/{total}] "
            f"fetching "
            f"{item['name']!r} "
            f"({human_bytes(item['bytes'])})",
            flush=True,
        )

        try:
            rsync_from_archive(
                remote_path=item[
                    "path"
                ],
                local_path=staging_path,
            )

            digest = local_sha256(
                staging_path
            )

            if digest != item["sha256"]:
                raise RuntimeError(
                    "SHA-256 mismatch after "
                    "Archive→Local transfer"
                )

            job_id = queue_ingest(
                staging_path=staging_path,
                original_filename=item[
                    "name"
                ],
            )

            print(
                f"  queued_job={job_id}",
                flush=True,
            )

            state = wait_for_job(
                job_id
            )

            imported += 1

            print(
                "  ready_photo="
                + str(
                    state[
                        "photo_id"
                    ]
                ),
                flush=True,
            )

        except Exception as exc:
            failures.append(
                {
                    "name": item[
                        "name"
                    ],
                    "error": (
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    ),
                }
            )

            print(
                "  ERROR "
                + failures[-1][
                    "error"
                ],
                flush=True,
            )

            # Stop rather than letting a storage,
            # archive, or worker problem cascade
            # across hundreds of originals.
            break

    return {
        "imported": imported,
        "skipped": skipped,
        "failures": failures,
    }


def cleanup_remote(
    remote_root: str,
) -> None:
    # remote_root is generated from our own fixed
    # archive base + SHA-256 prefix, never directly
    # from user-controlled path text.
    run_ssh(
        "rm -rf -- "
        + shlex.quote(
            remote_root
        )
    )


def print_preflight(
    result: dict,
) -> None:
    print()
    print(
        "=== ICLOUD IMPORT PREFLIGHT ==="
    )

    print(
        "new_supported_files="
        + str(
            result["new_files"]
        )
    )

    print(
        "already_in_library="
        + str(
            len(
                result[
                    "duplicates"
                ]
            )
        )
    )

    print(
        "unsupported_files="
        + str(
            len(
                result[
                    "unsupported"
                ]
            )
        )
    )

    print(
        "new_original_bytes="
        + human_bytes(
            result["new_bytes"]
        )
    )

    print(
        "largest_file="
        + human_bytes(
            result["largest_file"]
        )
    )

    print(
        "local_required="
        + human_bytes(
            result[
                "local_required"
            ]
        )
    )

    print(
        "local_headroom="
        + human_bytes(
            result[
                "local_headroom"
            ]
        )
    )

    print(
        "archive_required="
        + human_bytes(
            result[
                "archive_required"
            ]
        )
    )

    print(
        "archive_headroom="
        + human_bytes(
            result[
                "archive_headroom"
            ]
        )
    )

    print(
        "storage_preflight="
        + (
            "SAFE"
            if result["safe"]
            else "BLOCKED"
        )
    )


def get_url(
    explicit: str | None,
) -> str:
    if explicit:
        return validate_url(
            explicit
        )

    value = getpass.getpass(
        "Paste iCloud Link "
        "(input hidden): "
    )

    return validate_url(
        value
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Import a normal Apple iCloud "
            "Photos link into photo-curator"
        )
    )

    mode = parser.add_mutually_exclusive_group(
        required=True
    )

    mode.add_argument(
        "--probe",
        action="store_true",
        help=(
            "Open the link and verify that "
            "a bulk download is available"
        ),
    )

    mode.add_argument(
        "--import-all",
        action="store_true",
        help=(
            "Download, preflight, and ingest "
            "all supported photos"
        ),
    )

    parser.add_argument(
        "--url",
        help=(
            "Optional URL. Omitting this is "
            "recommended so the private link "
            "does not enter shell history."
        ),
    )

    parser.add_argument(
        "--keep-remote",
        action="store_true",
        help=(
            "Keep temporary iCloud download "
            "and extraction on Archive"
        ),
    )

    args = parser.parse_args()

    url = get_url(
        args.url
    )

    identifier = link_id(
        url
    )

    print(
        "icloud_link_id="
        + identifier
    )

    if args.probe:
        result = probe_link(
            url
        )

        print(
            "accessible="
            + str(
                result[
                    "accessible"
                ]
            ).lower()
        )

        print(
            "claimed_item_count="
            + str(
                result[
                    "claimed_item_count"
                ]
            )
        )

        print(
            "download_controls="
            + str(
                len(
                    result[
                        "download_controls"
                    ]
                )
            )
        )

        for label in result[
            "download_controls"
        ]:
            print(
                "  "
                + label
            )

        print(
            "ICLOUD_LINK_PROBE_COMPLETE"
        )

        return

    remote_root = remote_import_root(
        url
    )

    print(
        "browser_capture="
        "STARTING"
    )

    download_info = (
        capture_bulk_download(
            url
        )
    )

    print(
        "browser_capture="
        "COMPLETE"
    )

    remote_file = (
        download_to_archive(
            url=url,
            remote_root=remote_root,
            download_info=download_info,
        )
    )

    extracted = extract_remote(
        remote_file=remote_file,
        remote_root=remote_root,
    )

    print(
        "remote_inventory="
        "STARTING"
    )

    manifest = remote_manifest(
        extracted
    )

    print(
        "remote_inventory="
        "COMPLETE"
    )

    print(
        "remote_assets_found="
        + str(
            len(manifest)
        )
    )

    preflight = preflight_manifest(
        manifest
    )

    print_preflight(
        preflight
    )

    if not preflight["safe"]:
        print()
        print(
            "Import stopped before any "
            "new originals were queued."
        )

        print(
            "Remote iCloud staging was "
            "preserved for retry."
        )

        raise SystemExit(2)

    result = import_items(
        url=url,
        items=preflight[
            "supported"
        ],
    )

    print()
    print(
        "=== ICLOUD IMPORT RESULT ==="
    )

    print(
        "imported="
        + str(
            result["imported"]
        )
    )

    print(
        "skipped_duplicates="
        + str(
            result["skipped"]
        )
    )

    print(
        "failures="
        + str(
            len(
                result["failures"]
            )
        )
    )

    unsupported_count = len(
        preflight[
            "unsupported"
        ]
    )

    print(
        "unsupported="
        + str(
            unsupported_count
        )
    )

    should_cleanup = (
        not args.keep_remote
        and not result["failures"]
        and unsupported_count == 0
    )

    if should_cleanup:
        cleanup_remote(
            remote_root
        )

        print(
            "remote_staging="
            "CLEANED"
        )

    else:
        print(
            "remote_staging="
            "PRESERVED"
        )

        if unsupported_count:
            print(
                "Remote staging was kept "
                "because unsupported media "
                "was present."
            )

    print(
        "ICLOUD_LINK_IMPORT_COMPLETE"
    )


if __name__ == "__main__":
    main()
