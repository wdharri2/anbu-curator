# Copyright (c) 2026 Willie D. Harris, Jr.
# SPDX-License-Identifier: LicenseRef-Anbu-Source-Available-1.0

from __future__ import annotations

import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import (
    APIRouter,
    HTTPException,
)
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
)
from PIL import Image, ImageOps
from pydantic import BaseModel

from curator.db import connect
from curator.storage.lifecycle import visual_path


load_dotenv(
    ".env",
    override=True,
)

router = APIRouter()

THUMBNAIL_ROOT = Path(
    os.environ["CURATOR_THUMBNAILS"]
)

THUMBNAIL_ROOT.mkdir(
    parents=True,
    exist_ok=True,
)


class PublishRequest(BaseModel):
    photo_ids: list[int]
    dispositions: dict[int, str] = {}
    caption_final: str | None = None
    notes: str | None = None
    posted_at: str | None = None


class RejectRequest(BaseModel):
    dispositions: dict[int, str] = {}
    notes: str | None = None


def get_candidate(
    post_id: int,
) -> dict:
    with connect() as db:
        row = db.execute(
            """
            SELECT *
            FROM post_candidates
            WHERE id = ?
            """,
            (post_id,),
        ).fetchone()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Post candidate not found",
        )

    return dict(row)


def get_candidate_photos(
    post_id: int,
) -> list[dict]:
    with connect() as db:
        rows = db.execute(
            """
            SELECT
                pcp.photo_id,
                pcp.position,
                pcp.is_cover,
                pcp.role,
                pcp.inclusion_reason,
                pcp.review_disposition,

                p.filename,
                p.captured_at,

                (
                    SELECT COUNT(*)
                    FROM published_post_photos ppp
                    WHERE ppp.photo_id = p.id
                ) AS times_published,

                (
                    SELECT MAX(pp.posted_at)
                    FROM published_post_photos ppp
                    JOIN published_posts pp
                      ON pp.id = ppp.published_post_id
                    WHERE ppp.photo_id = p.id
                ) AS last_published_at

            FROM post_candidate_photos pcp

            JOIN photos p
              ON p.id = pcp.photo_id

            WHERE pcp.post_id = ?

            ORDER BY pcp.position
            """,
            (post_id,),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def get_candidate_neighbors(
    post_id: int,
) -> tuple[int | None, int | None]:
    with connect() as db:
        previous_row = db.execute(
            """
            SELECT id
            FROM post_candidates
            WHERE
                status = 'candidate'
                AND id > ?
            ORDER BY id ASC
            LIMIT 1
            """,
            (post_id,),
        ).fetchone()

        next_row = db.execute(
            """
            SELECT id
            FROM post_candidates
            WHERE
                status = 'candidate'
                AND id < ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (post_id,),
        ).fetchone()

    previous_id = (
        previous_row["id"]
        if previous_row
        else None
    )

    next_id = (
        next_row["id"]
        if next_row
        else None
    )

    return previous_id, next_id


def thumb_path(
    photo_id: int,
) -> Path:
    return (
        THUMBNAIL_ROOT
        / f"review-{photo_id}.jpg"
    )


def ensure_thumbnail(
    photo_id: int,
) -> Path:
    output = thumb_path(
        photo_id
    )

    # Existing cache entries are valid only when
    # they contain actual image data.
    if output.exists():
        if output.stat().st_size > 0:
            return output

        output.unlink()

    try:
        source = visual_path(
            photo_id
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="No local visual file available",
        )

    # Write to a temporary sibling first. Only move
    # it into the cache after generation succeeds.
    temporary = output.with_name(
        f".{output.name}.{photo_id}.tmp"
    )

    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(
                opened
            ).convert("RGB")

        image.thumbnail(
            (1000, 1000)
        )

        image.save(
            temporary,
            "JPEG",
            quality=88,
        )

        if (
            not temporary.exists()
            or temporary.stat().st_size == 0
        ):
            raise RuntimeError(
                "Thumbnail generation produced "
                "an empty file"
            )

        temporary.replace(
            output
        )

    finally:
        if temporary.exists():
            temporary.unlink()

    return output


def layout(
    title: str,
    body: str,
) -> str:
    return f"""
<!doctype html>
<html>
<head>
<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
>
<title>{html.escape(title)}</title>

<style>
:root {{
    color-scheme: dark;
}}

* {{
    box-sizing: border-box;
}}

body {{
    margin: 0;
    background: #111;
    color: #f4f4f4;
    font-family:
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;
}}

header {{
    position: sticky;
    top: 0;
    z-index: 10;
    background: rgba(17,17,17,.95);
    backdrop-filter: blur(12px);
    border-bottom: 1px solid #333;
    padding: 14px 16px;
}}

header a {{
    color: white;
    text-decoration: none;
    font-weight: 700;
}}

main {{
    max-width: 900px;
    margin: auto;
    padding: 18px;
}}

.card {{
    display: block;
    background: #1d1d1d;
    border: 1px solid #333;
    border-radius: 16px;
    margin-bottom: 18px;
    overflow: hidden;
    color: white;
    text-decoration: none;
}}

.card-content {{
    padding: 15px;
}}

img {{
    width: 100%;
    display: block;
    background: #222;
}}

.badge {{
    display: inline-block;
    background: #333;
    border-radius: 999px;
    padding: 5px 9px;
    margin: 0 5px 5px 0;
    font-size: 12px;
}}

.status-published {{
    background: #174e2b;
}}

.status-rejected {{
    background: #552125;
}}

.status-candidate {{
    background: #263e62;
}}

.warning {{
    background: #5a431a;
}}

.photo {{
    background: #1d1d1d;
    border: 1px solid #333;
    border-radius: 15px;
    margin: 20px 0;
    overflow: hidden;
}}

.photo-info {{
    padding: 14px;
}}

.controls {{
    display: grid;
    grid-template-columns: 1fr 90px;
    gap: 10px;
    margin: 10px 0;
}}

input,
textarea,
button {{
    font: inherit;
}}

input[type="number"],
textarea {{
    width: 100%;
    padding: 12px;
    border: 1px solid #444;
    background: #161616;
    color: white;
    border-radius: 10px;
}}

textarea {{
    min-height: 100px;
}}

button {{
    width: 100%;
    padding: 15px;
    margin-top: 10px;
    border: 0;
    border-radius: 12px;
    font-weight: 700;
}}

.publish {{
    background: #e8e8e8;
    color: #111;
}}

.reject {{
    background: #572529;
    color: white;
}}

.muted {{
    color: #aaa;
    font-size: 14px;
    line-height: 1.5;
}}

.reason {{
    line-height: 1.55;
}}

h1 {{
    font-size: 25px;
}}

h2 {{
    font-size: 20px;
}}


.photo-disposition {{
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 10px;
}}

.disposition-button {{
    appearance: none;
    border: 1px solid #555;
    border-radius: 8px;
    padding: 10px 12px;
    font-weight: 700;
    cursor: pointer;
    opacity: 0.55;
}}

.disposition-button.selected {{
    opacity: 1;
    border-width: 2px;
}}

.disposition-button[data-value="use"].selected {{
    background: #173d24;
}}

.disposition-button[data-value="recurate"].selected {{
    background: #4a3b12;
}}

.disposition-button[data-value="suppress"].selected {{
    background: #4a1818;
}}


.candidate-navigation {{
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    margin: 12px 0 18px;
}}

.candidate-navigation a,
.candidate-navigation span {{
    padding: 10px 12px;
    border: 1px solid #555;
    border-radius: 8px;
    text-decoration: none;
}}

.candidate-navigation span {{
    opacity: 0.35;
}}

</style>
</head>

<body>

<header>
<a href="/posts">Photo Curator · Posts</a>
</header>

<main>
{body}
</main>

</body>
</html>
"""


@router.get(
    "/posts/photo/{photo_id}/thumb"
)
def photo_thumb(
    photo_id: int,
):
    path = ensure_thumbnail(
        photo_id
    )

    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={
            "Cache-Control":
                "no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )


@router.get(
    "/api/posts"
)
def api_posts():
    with connect() as db:
        rows = db.execute(
            """
            SELECT
                pc.*,

                (
                    SELECT COUNT(*)
                    FROM post_candidate_photos pcp
                    WHERE pcp.post_id = pc.id
                ) AS photo_count,

                (
                    SELECT pcp.photo_id
                    FROM post_candidate_photos pcp
                    WHERE pcp.post_id = pc.id
                    ORDER BY
                        pcp.is_cover DESC,
                        pcp.position
                    LIMIT 1
                ) AS preview_photo_id

            FROM post_candidates pc

            WHERE pc.status != 'superseded'

            ORDER BY pc.created_at DESC
            """
        ).fetchall()

    return {
        "posts": [
            dict(row)
            for row in rows
        ]
    }


@router.get(
    "/api/posts/{post_id}"
)
def api_post(
    post_id: int,
):
    candidate = get_candidate(
        post_id
    )

    photos = get_candidate_photos(
        post_id
    )

    with connect() as db:
        published = db.execute(
            """
            SELECT *
            FROM published_posts
            WHERE candidate_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (post_id,),
        ).fetchone()

        actual_photos = []

        if published:
            rows = db.execute(
                """
                SELECT
                    ppp.photo_id,
                    ppp.position,
                    ppp.is_cover,
                    p.filename
                FROM published_post_photos ppp
                JOIN photos p
                  ON p.id = ppp.photo_id
                WHERE ppp.published_post_id = ?
                ORDER BY ppp.position
                """,
                (published["id"],),
            ).fetchall()

            actual_photos = [
                dict(row)
                for row in rows
            ]

    return {
        "candidate": candidate,
        "photos": photos,
        "published": (
            dict(published)
            if published
            else None
        ),
        "actual_photos": actual_photos,
    }


VALID_PHOTO_DISPOSITIONS = {
    "use",
    "recurate",
    "suppress",
}


def save_editorial_feedback(
    db,
    post_id: int,
    notes: str | None,
) -> None:
    if notes is None:
        return

    cleaned = notes.strip()

    db.execute(
        """
        UPDATE post_candidates
        SET
            editorial_feedback = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            cleaned or None,
            post_id,
        ),
    )


def apply_photo_dispositions(
    db,
    post_id: int,
    dispositions: dict[int, str],
) -> None:
    if not dispositions:
        return

    candidate_photo_ids = {
        row["photo_id"]
        for row in db.execute(
            """
            SELECT photo_id
            FROM post_candidate_photos
            WHERE post_id = ?
            """,
            (post_id,),
        ).fetchall()
    }

    supplied_ids = set(dispositions)

    unknown_ids = (
        supplied_ids
        - candidate_photo_ids
    )

    if unknown_ids:
        raise HTTPException(
            status_code=400,
            detail=(
                "Disposition supplied for photos "
                "outside candidate: "
                f"{sorted(unknown_ids)}"
            ),
        )

    invalid = {
        photo_id: disposition
        for photo_id, disposition
        in dispositions.items()
        if disposition
        not in VALID_PHOTO_DISPOSITIONS
    }

    if invalid:
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid photo dispositions: "
                f"{invalid}"
            ),
        )

    state_map = {
        "use": "eligible",
        "recurate": "recurate",
        "suppress": "suppressed",
    }

    for photo_id, disposition in (
        dispositions.items()
    ):
        db.execute(
            """
            UPDATE post_candidate_photos
            SET review_disposition = ?
            WHERE
                post_id = ?
                AND photo_id = ?
            """,
            (
                disposition,
                post_id,
                photo_id,
            ),
        )

        db.execute(
            """
            UPDATE photos
            SET
                curation_state = ?,
                curation_state_updated_at =
                    CURRENT_TIMESTAMP,
                updated_at =
                    CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                state_map[disposition],
                photo_id,
            ),
        )


@router.post(
    "/api/posts/{post_id}/publish"
)
def publish_post(
    post_id: int,
    payload: PublishRequest,
):
    candidate = get_candidate(
        post_id
    )

    if candidate["status"] == "published":
        raise HTTPException(
            status_code=409,
            detail="Candidate is already published",
        )

    if not payload.photo_ids:
        raise HTTPException(
            status_code=400,
            detail="Select at least one photo",
        )

    if (
        len(payload.photo_ids)
        != len(set(payload.photo_ids))
    ):
        raise HTTPException(
            status_code=400,
            detail="Duplicate photo IDs supplied",
        )

    candidate_photos = {
        row["photo_id"]
        for row in get_candidate_photos(
            post_id
        )
    }

    selected = set(
        payload.photo_ids
    )

    unknown = (
        selected
        - candidate_photos
    )

    if unknown:
        raise HTTPException(
            status_code=400,
            detail=(
                "Photos were not part of "
                f"candidate: {sorted(unknown)}"
            ),
        )

    posted_at = (
        payload.posted_at
        or datetime.now(
            timezone.utc
        ).isoformat()
    )

    with connect() as db:
        apply_photo_dispositions(
            db,
            post_id,
            payload.dispositions,
        )

        if payload.dispositions:
            expected_selected = {
                photo_id
                for photo_id, disposition
                in payload.dispositions.items()
                if disposition == "use"
            }

            if selected != expected_selected:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Published photo IDs must "
                        "match photos marked use"
                    ),
                )

        existing = db.execute(
            """
            SELECT id
            FROM published_posts
            WHERE candidate_id = ?
            """,
            (post_id,),
        ).fetchone()

        if existing:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Publication already recorded"
                ),
            )

        cursor = db.execute(
            """
            INSERT INTO published_posts (
                candidate_id,
                posted_at,
                caption_final,
                notes
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                post_id,
                posted_at,
                payload.caption_final,
                payload.notes,
            ),
        )

        published_post_id = (
            cursor.lastrowid
        )

        for position, photo_id in enumerate(
            payload.photo_ids,
            start=1,
        ):
            db.execute(
                """
                INSERT INTO published_post_photos (
                    published_post_id,
                    photo_id,
                    position,
                    is_cover
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    published_post_id,
                    photo_id,
                    position,
                    1 if position == 1 else 0,
                ),
            )

        db.execute(
            """
            UPDATE post_candidates
            SET
                status = 'published',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (post_id,),
        )

        db.commit()

    return {
        "ok": True,
        "published_post_id": (
            published_post_id
        ),
        "photo_ids": payload.photo_ids,
        "cover_photo_id": (
            payload.photo_ids[0]
        ),
    }


@router.post(
    "/api/posts/{post_id}/recurate"
)
def recurate_post(
    post_id: int,
    payload: RejectRequest | None = None,
):
    candidate = get_candidate(
        post_id
    )

    if candidate["status"] == "published":
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot recurate a recorded "
                "published post"
            ),
        )

    with connect() as db:
        if payload is not None:
            save_editorial_feedback(
                db,
                post_id,
                payload.notes,
            )

            apply_photo_dispositions(
                db,
                post_id,
                payload.dispositions,
            )

        rows = db.execute(
            """
            SELECT
                photo_id,
                review_disposition
            FROM post_candidate_photos
            WHERE post_id = ?
            """,
            (post_id,),
        ).fetchall()

        for row in rows:
            if row["review_disposition"] == "suppress":
                continue

            db.execute(
                """
                UPDATE post_candidate_photos
                SET review_disposition = 'recurate'
                WHERE
                    post_id = ?
                    AND photo_id = ?
                """,
                (
                    post_id,
                    row["photo_id"],
                ),
            )

            db.execute(
                """
                UPDATE photos
                SET
                    curation_state = 'recurate',
                    curation_state_updated_at =
                        CURRENT_TIMESTAMP,
                    updated_at =
                        CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (row["photo_id"],),
            )

        db.execute(
            """
            UPDATE post_candidates
            SET
                status = 'recurate',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (post_id,),
        )

        db.commit()

    return {
        "ok": True,
        "status": "recurate",
    }


@router.post(
    "/api/posts/{post_id}/reject"
)
def reject_post(
    post_id: int,
    payload: RejectRequest | None = None,
):
    candidate = get_candidate(
        post_id
    )

    if candidate["status"] == "published":
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot reject a recorded "
                "published post"
            ),
        )

    with connect() as db:
        if payload is not None:
            save_editorial_feedback(
                db,
                post_id,
                payload.notes,
            )

            apply_photo_dispositions(
                db,
                post_id,
                payload.dispositions,
            )

        db.execute(
            """
            UPDATE post_candidates
            SET
                status = 'rejected',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (post_id,),
        )
        db.commit()

    return {
        "ok": True,
        "status": "rejected",
    }


@router.get(
    "/posts/published",
    response_class=HTMLResponse,
)
def published_posts_page():
    return posts_page(
        status="published"
    )


@router.get(
    "/posts/rejected",
    response_class=HTMLResponse,
)
def rejected_posts_page():
    return posts_page(
        status="rejected"
    )


@router.get(
    "/posts/recurated",
    response_class=HTMLResponse,
)
def recurated_posts_page():
    return posts_page(
        status="recurate"
    )


@router.get(
    "/posts",
    response_class=HTMLResponse,
)
def posts_page(
    status: str = "candidate",
):
    valid_statuses = {
        "candidate",
        "published",
        "rejected",
        "recurate",
    }

    if status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail="Invalid post status",
        )

    data = [
        post
        for post in api_posts()["posts"]
        if post["status"] == status
    ]

    if not data:
        return HTMLResponse(
            layout(
                "Curated Posts",
                """
                <h1>Curated Posts</h1>
                <p class="muted">
                No post candidates yet.
                </p>
                """,
            )
        )

    cards = []

    for post in data:
        preview = ""

        if post["preview_photo_id"]:
            preview = (
                f'<img src="/posts/photo/'
                f'{post["preview_photo_id"]}'
                f'/thumb">'
            )

        status = html.escape(
            post["status"]
        )

        cards.append(
            f"""
            <a
                class="card"
                href="/posts/{post['id']}"
            >
                {preview}

                <div class="card-content">

                    <span
                        class="badge status-{status}"
                    >
                        {status}
                    </span>

                    <span class="badge">
                        {html.escape(
                            post["post_type"]
                            or "other"
                        )}
                    </span>

                    <span class="badge">
                        {post["photo_count"]} photos
                    </span>

                    <h2>
                        {html.escape(
                            post["title"]
                            or f"Post {post['id']}"
                        )}
                    </h2>

                    <p class="muted">
                        {html.escape(
                            post["story_summary"]
                            or ""
                        )}
                    </p>

                    <p class="muted">
                        Created {html.escape(
                            post["created_at"]
                        )}
                    </p>

                </div>
            </a>
            """
        )

    body = (
        """
        <h1>Curated Posts</h1>
        <p class="muted">
        Candidates remain here after publication
        so this page also becomes the curator's
        editorial history.
        </p>
        """
        + "\n".join(cards)
    )

    return HTMLResponse(
        layout(
            "Curated Posts",
            body,
        )
    )


@router.get(
    "/posts/{post_id}",
    response_class=HTMLResponse,
)
def post_page(
    post_id: int,
):
    candidate = get_candidate(
        post_id
    )

    photos = get_candidate_photos(
        post_id
    )

    previous_id, next_id = (
        get_candidate_neighbors(
            post_id
        )
    )

    scores = {}

    if candidate["scores_json"]:
        scores = json.loads(
            candidate["scores_json"]
        )

    score_badges = "".join(
        f"""
        <span class="badge">
            {html.escape(str(key))}:
            {html.escape(str(value))}
        </span>
        """
        for key, value in scores.items()
    )

    previous_link = (
        f'<a href="/posts/{previous_id}">'
        '← Previous</a>'
        if previous_id is not None
        else '<span>← Previous</span>'
    )

    next_link = (
        f'<a href="/posts/{next_id}">'
        'Next →</a>'
        if next_id is not None
        else '<span>Next →</span>'
    )

    navigation_html = f"""
    <div class="candidate-navigation">
        <a href="/posts">
            Back to posts
        </a>

        {previous_link}

        {next_link}
    </div>
    """

    photo_html = []

    for photo in photos:
        used_badge = ""

        if photo["times_published"]:
            used_badge = f"""
            <span class="badge warning">
                USED BEFORE ×
                {photo["times_published"]}
                {html.escape(
                    str(
                        photo[
                            "last_published_at"
                        ]
                        or ""
                    )
                )}
            </span>
            """

        cover_badge = (
            '<span class="badge">'
            'cover candidate'
            '</span>'
            if photo["is_cover"]
            else ""
        )

        disposition = (
            photo["review_disposition"]
            or "use"
        )

        use_selected = (
            " selected"
            if disposition == "use"
            else ""
        )

        recurate_selected = (
            " selected"
            if disposition == "recurate"
            else ""
        )

        suppress_selected = (
            " selected"
            if disposition == "suppress"
            else ""
        )

        photo_html.append(
            f"""
            <div
                class="photo"
                data-photo-id="{photo['photo_id']}"
            >
                <img
                    src="/posts/photo/{photo['photo_id']}/thumb?v=2"
                >

                <div class="photo-info">

                    <div>
                        <span class="badge">
                            Photo {photo['photo_id']}
                        </span>

                        <span class="badge">
                            {html.escape(
                                photo["role"]
                                or "photo"
                            )}
                        </span>

                        {cover_badge}
                        {used_badge}
                    </div>

                    <h2>
                        {html.escape(
                            photo["filename"]
                        )}
                    </h2>

                    <div class="controls">

                        <div
                            class="photo-disposition"
                            data-disposition="{disposition}"
                        >
                            <button
                                type="button"
                                class="disposition-button{use_selected}"
                                data-value="use"
                                onclick="setDisposition(this)"
                            >
                                USE HERE
                            </button>

                            <button
                                type="button"
                                class="disposition-button{recurate_selected}"
                                data-value="recurate"
                                onclick="setDisposition(this)"
                            >
                                RECURATE
                            </button>

                            <button
                                type="button"
                                class="disposition-button{suppress_selected}"
                                data-value="suppress"
                                onclick="setDisposition(this)"
                            >
                                SUPPRESS
                            </button>
                        </div>

                        <input
                            class="position"
                            type="number"
                            min="1"
                            value="{photo['position']}"
                        >

                    </div>

                    <p class="reason">
                        {html.escape(
                            photo[
                                "inclusion_reason"
                            ]
                            or ""
                        )}
                    </p>

                </div>
            </div>
            """
        )

    status = candidate["status"]

    actions = ""

    if status == "candidate":
        caption = html.escape(
            candidate["caption_draft"]
            or ""
        )

        actions = f"""
        <h2>Record what you actually posted</h2>

        <p class="muted">
        Uncheck anything you did not use and
        change the position numbers to match
        your actual carousel. Position 1 is
        recorded as the published cover.
        </p>

        <label>Final caption</label>
        <textarea id="caption">{caption}</textarea>

        <label>Notes</label>
        <textarea
            id="notes"
            placeholder="Optional editorial notes"
        ></textarea>

        <button
            class="publish"
            onclick="publishPost()"
        >
            Mark Published
        </button>

        
        <button
            type="button"
            onclick="recuratePost()"
        >
            Recurate Candidate
        </button>

        <button
            class="reject"
            onclick="rejectPost()"
        >
            Reject Candidate
        </button>

        <script>
        const POST_ID = {post_id};
        const NEXT_ID = {next_id if next_id is not None else "null"};

        function goToNextCandidate() {{
            if (NEXT_ID !== null) {{
                location.href = `/posts/${{NEXT_ID}}`;
            }} else {{
                location.href = "/posts";
            }}
        }}

        function setDisposition(button) {{
            const group = button.closest(
                ".photo-disposition"
            );

            const value =
                button.dataset.value;

            group.dataset.disposition =
                value;

            group.querySelectorAll(
                ".disposition-button"
            ).forEach(
                item =>
                    item.classList.toggle(
                        "selected",
                        item === button
                    )
            );
        }}

        function getPhotoReviewState() {{
            const photos = [
                ...document.querySelectorAll(
                    ".photo"
                )
            ];

            const dispositions = {{}};

            const selected = photos
                .map(
                    el => {{
                        const id = Number(
                            el.dataset.photoId
                        );

                        const disposition =
                            el.querySelector(
                                ".photo-disposition"
                            ).dataset.disposition;

                        dispositions[id] =
                            disposition;

                        return {{
                            id,
                            disposition,
                            position: Number(
                                el.querySelector(
                                    ".position"
                                ).value
                            )
                        }};
                    }}
                )
                .filter(
                    item =>
                        item.disposition === "use"
                )
                .sort(
                    (a, b) =>
                        a.position - b.position
                )
                .map(
                    item => item.id
                );

            return {{
                selected,
                dispositions
            }};
        }}

        async function publishPost() {{
            const {{
                selected,
                dispositions
            }} = getPhotoReviewState();

            if (!selected.length) {{
                alert(
                    "At least one photo must be "
                    + "marked USE HERE."
                );
                return;
            }}

            const response = await fetch(
                `/api/posts/${{POST_ID}}/publish`,
                {{
                    method: "POST",
                    headers: {{
                        "Content-Type":
                            "application/json"
                    }},
                    body: JSON.stringify({{
                        photo_ids: selected,
                        dispositions: dispositions,
                        caption_final:
                            document.getElementById(
                                "caption"
                            ).value,
                        notes:
                            document.getElementById(
                                "notes"
                            ).value
                    }})
                }}
            );

            if (!response.ok) {{
                alert(
                    await response.text()
                );
                return;
            }}

            location.reload();
        }}

        async function recuratePost() {{
            if (
                !confirm(
                    "Recurate this candidate?"
                )
            ) {{
                return;
            }}

            const {{
                dispositions
            }} = getPhotoReviewState();

            const response = await fetch(
                `/api/posts/${{POST_ID}}/recurate`,
                {{
                    method: "POST",
                    headers: {{
                        "Content-Type":
                            "application/json"
                    }},
                    body: JSON.stringify({{
                        dispositions:
                            dispositions,
                        notes:
                            document.getElementById(
                                "notes"
                            ).value
                    }})
                }}
            );

            if (!response.ok) {{
                alert(
                    await response.text()
                );
                return;
            }}

            goToNextCandidate();
        }}

        async function rejectPost() {{
            if (
                !confirm(
                    "Reject this candidate?"
                )
            ) {{
                return;
            }}

            const {{
                dispositions
            }} = getPhotoReviewState();

            const response = await fetch(
                `/api/posts/${{POST_ID}}/reject`,
                {{
                    method: "POST",
                    headers: {{
                        "Content-Type":
                            "application/json"
                    }},
                    body: JSON.stringify({{
                        dispositions:
                            dispositions,
                        notes:
                            document.getElementById(
                                "notes"
                            ).value
                    }})
                }}
            );

            if (!response.ok) {{
                alert(
                    await response.text()
                );
                return;
            }}

            goToNextCandidate();
        }}
        </script>
        """

    elif status == "published":
        with connect() as db:
            publication = db.execute(
                """
                SELECT *
                FROM published_posts
                WHERE candidate_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (post_id,),
            ).fetchone()

            actual = []

            if publication:
                actual = db.execute(
                    """
                    SELECT
                        ppp.position,
                        ppp.photo_id,
                        p.filename
                    FROM published_post_photos ppp
                    JOIN photos p
                      ON p.id = ppp.photo_id
                    WHERE ppp.published_post_id = ?
                    ORDER BY ppp.position
                    """,
                    (publication["id"],),
                ).fetchall()

        actual_list = "".join(
            f"""
            <li>
            {row["position"]}.
            {html.escape(row["filename"])}
            · Photo {row["photo_id"]}
            </li>
            """
            for row in actual
        )

        actions = f"""
        <div class="card-content">
        <h2>Recorded publication</h2>
        <p class="muted">
            {html.escape(
                publication["posted_at"]
                if publication
                else ""
            )}
        </p>
        <ol>{actual_list}</ol>
        </div>
        """

    else:
        actions = """
        <p class="muted">
        This candidate was rejected.
        Its photos remain available to the
        curator for future work.
        </p>
        """

    body = f"""
    <div>
        <span
            class="badge status-{html.escape(status)}"
        >
            {html.escape(status)}
        </span>

        <span class="badge">
            {html.escape(
                candidate["post_type"]
                or "other"
            )}
        </span>
    </div>

    <h1>
        {html.escape(
            candidate["title"]
            or f"Post {post_id}"
        )}
    </h1>

    <p class="reason">
        {html.escape(
            candidate["story_summary"]
            or ""
        )}
    </p>

    <details>
        <summary>
            Curator reasoning
        </summary>

        <p class="reason">
            {html.escape(
                candidate[
                    "curator_reasoning"
                ]
                or ""
            )}
        </p>
    </details>

    <p>{score_badges}</p>

    <h2>Proposed carousel</h2>

    {navigation_html}
{''.join(photo_html)}

    {actions}
    """

    return HTMLResponse(
        layout(
            candidate["title"]
            or "Curated Post",
            body,
        )
    )
