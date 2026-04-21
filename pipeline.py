"""
End-to-end pipeline: NL prompt → Photos query → curation → slideshow.
"""
from __future__ import annotations

import datetime as _dt
import os
import random as _random
import shutil
import tempfile
from pathlib import Path
from typing import Callable, Optional

import osxphotos

from claude_client import prompt_to_filter_spec, curate_photos
from generators import generate_output


ProgressFn = Optional[Callable[[str], None]]


def run_pipeline(params: dict, output_dir: Path, on_progress: ProgressFn = None) -> Path:
    """
    Returns the path to the produced slideshow (or report, for album mode).
    """
    progress = on_progress or (lambda _t: None)

    prompt: str = params["prompt"]
    fmt: str = params["format"]
    max_photos: int = int(params["max_photos"])
    visual_curation: bool = bool(params.get("visual_curation", True))

    # ---- 1. Filter spec ----------------------------------------------------
    progress("🧠")
    if "_filter_spec" in params:
        # User previewed and (optionally) edited the spec in the dialog.
        spec = params["_filter_spec"]
    else:
        spec = prompt_to_filter_spec(
            prompt=prompt,
            target_count=max_photos,
            current_date=_dt.date.today().isoformat(),
        )

    # ---- 2. Query Photos library ------------------------------------------
    progress("🔎")

    # Determine scan limit from user choice in dialog.
    if params.get("scan_mode") == "date_range":
        # User chose a date range — override the Claude-generated date_range.
        dr = params.get("scan_date_range") or {}
        if dr.get("start") or dr.get("end"):
            spec["date_range"] = dr
        # Always fetch everything in the range; random pick (if on) trims after.
        limit = 50_000
    elif params.get("scan_mode") == "count":
        # Explicit photo count to scan. If random, fetch 50k first so the
        # shuffle is truly random across all matches, not just the first N.
        limit = max(int(params.get("scan_limit", 400)), max_photos)
        if params.get("random_sample"):
            limit = 50_000
    else:
        # Legacy / CLI default: Claude's limit_candidates, capped at 400.
        limit = int(spec.get("limit_candidates") or max_photos * 4)
        limit = min(max(limit, max_photos), 400)

    photos = query_photos(spec, limit=limit)

    # Random sampling — only shuffles photos that already matched the filter;
    # never injects unrelated photos. Trims to random_pick_count (or scan_limit
    # for count mode, falling back to 400).
    if params.get("random_sample") and photos:
        pick = max(
            int(params.get("random_pick_count",
                           params.get("scan_limit", 400))),
            max_photos,
        )
        _random.shuffle(photos)
        photos = photos[:pick]

    if not photos:
        # Fallback: replace places (needs GPS) with album/keyword substring
        # matches using the prompt words, then text-search, then (if vision is
        # on) a broad pool for Claude to curate visually.
        progress("🔎\u207b")
        photos = _fallback_query(spec, limit, prompt, visual_curation=visual_curation)

    if not photos:
        import json as _json
        raise RuntimeError(
            "No photos matched those filters.\n\n"
            f"Filter tried:\n{_json.dumps(spec, indent=2)}\n\n"
            "Tips:\n"
            "• Use 'Preview Filter' in the dialog to edit the filter before generating.\n"
            "• Check that your Morocco photos have an album or keyword named 'Morocco'.\n"
            "• Without GPS location data, place-based filters won't match — "
            "try album or keyword names instead."
        )

    # ---- 3. Build lightweight candidate records (with thumbs for vision) ---
    progress("🖼️")
    thumb_dir = Path(tempfile.mkdtemp(prefix="slideshow_thumbs_"))
    try:
        records = _build_candidate_records(photos, thumb_dir, make_thumbs=visual_curation)

        # ---- 4. Curate -----------------------------------------------------
        progress("🎨")
        if len(records) <= max_photos:
            # Nothing to curate down to; chronological order.
            ordered_uuids = [r["uuid"] for r in sorted(records, key=lambda x: x["date"] or "")]
        else:
            ordered_uuids, _rationale = curate_photos(
                prompt=prompt,
                candidates=records,
                target_count=max_photos,
                use_vision=visual_curation,
            )

        by_uuid = {p.uuid: p for p in photos}
        ordered_photos = [by_uuid[u] for u in ordered_uuids if u in by_uuid]
        if not ordered_photos:
            raise RuntimeError("Curation returned no matching photos from the candidate set.")

        # ---- 5. Generate output -------------------------------------------
        progress("🎬")
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in prompt)[:40].strip()
        base_name = f"{ts}_{safe}".replace(" ", "_") or ts

        return generate_output(
            photos=ordered_photos,
            output_dir=output_dir,
            base_name=base_name,
            output_format=fmt,
            prompt=prompt,
        )
    finally:
        shutil.rmtree(thumb_dir, ignore_errors=True)


# --------------------------------------------------------------------- query

def _fallback_query(spec: dict, limit: int, prompt: str, visual_curation: bool = False) -> list:
    """
    When the strict spec returns nothing, try progressively looser strategies:

    1. Keyword-only OR album-only search using prompt words (separate queries,
       merged — avoids the osxphotos AND-between-fields trap).
    2. Text-search photo metadata; only return photos that scored > 0.
    3. If visual_curation is on, return a broad candidate pool and let Claude
       vision identify matches from thumbnails (e.g. for untagged sunsets).
    """
    words = [w.lower().strip(".,!?") for w in prompt.split() if len(w) >= 3]

    # Strategy 1: keyword OR album substring match — run separately and merge.
    if words:
        base = {
            "date_range":  spec.get("date_range"),
            "persons":     spec.get("persons"),
            "places":      None,
            "favorites_only": False,
            "has_gps":     None,
            "orientation": spec.get("orientation"),
            "media_type":  spec.get("media_type", "any"),
            "limit_candidates": limit,
        }
        kw_photos = query_photos({**base, "keywords": words, "albums": None}, limit=limit)
        al_photos = query_photos({**base, "keywords": None, "albums": words}, limit=limit)
        # Merge, deduplicate by UUID, preserve chronological order.
        seen: set = set()
        merged = []
        for p in kw_photos + al_photos:
            if p.uuid not in seen:
                seen.add(p.uuid)
                merged.append(p)
        merged.sort(key=lambda p: (p.date or _dt.datetime.min))
        if merged:
            return merged[:limit]

    # Strategy 2: text-search photo metadata — only return actual matches.
    s2 = {
        "date_range": spec.get("date_range"),
        "favorites_only": False,
        "has_gps": None,
        "orientation": None,
        "media_type": spec.get("media_type", "any"),
        "limit_candidates": 400,
    }
    candidates = query_photos(s2, limit=400)
    if not candidates or not words:
        return []

    def _text_of(p) -> str:
        parts = []
        if getattr(p, "place", None) and p.place:
            parts.append(p.place.name or "")
        parts.extend(str(k) for k in (p.keywords or []))
        for a in (p.albums or []):
            parts.append(a if isinstance(a, str) else getattr(a, "title", "") or "")
        parts.append(p.filename or "")
        return " ".join(parts).lower()

    scored = [(sum(w in _text_of(p) for w in words), p) for p in candidates]
    scored.sort(key=lambda x: -x[0])
    # Only return photos that actually scored — never dump unrelated photos.
    matched = [p for score, p in scored if score > 0]
    if matched:
        return matched[:limit]

    # Strategy 3: visual curation fallback — return a broad recent pool and let
    # Claude vision find the matches from thumbnails (works for untagged subjects
    # like "sunset" where no metadata exists but the images are visually distinct).
    if visual_curation:
        return candidates[:limit]  # candidates already loaded above

    return []


def query_photos(spec: dict, limit: int) -> list:
    """Apply the filter spec against the Photos library via osxphotos."""
    db = osxphotos.PhotosDB()

    # Build QueryOptions defensively — osxphotos has evolved, so catch attr errors.
    q = osxphotos.QueryOptions()

    dr = spec.get("date_range") or {}
    if dr.get("start"):
        q.from_date = _dt.datetime.fromisoformat(dr["start"])
    if dr.get("end"):
        end = _dt.datetime.fromisoformat(dr["end"])
        q.to_date = end.replace(hour=23, minute=59, second=59)

    if spec.get("albums"):
        q.album = list(spec["albums"])
    if spec.get("keywords"):
        q.keyword = list(spec["keywords"])
    if spec.get("persons"):
        q.person = list(spec["persons"])
    if spec.get("places"):
        q.place = list(spec["places"])
    if spec.get("favorites_only"):
        q.favorite = True
    if spec.get("has_gps"):
        q.has_location = True

    mt = (spec.get("media_type") or "any").lower()
    if mt == "photo":
        q.photos = True
        q.movies = False
    elif mt == "video":
        q.photos = False
        q.movies = True
    # else: leave defaults (both)

    photos = db.query(q)

    # Orientation filter — applied post-query because osxphotos doesn't expose it.
    orientation = spec.get("orientation")
    if orientation in {"landscape", "portrait", "square"}:
        photos = [p for p in photos if _matches_orientation(p, orientation)]

    # Drop hidden/trashed
    photos = [p for p in photos if not getattr(p, "hidden", False) and not getattr(p, "intrash", False)]

    # Stable sort by date ascending
    photos.sort(key=lambda p: (p.date or _dt.datetime.min))
    return photos[:limit]


def _matches_orientation(p, orientation: str) -> bool:
    w, h = getattr(p, "width", None), getattr(p, "height", None)
    if not w or not h:
        return False
    if orientation == "landscape":
        return w > h
    if orientation == "portrait":
        return h > w
    if orientation == "square":
        return abs(w - h) < min(w, h) * 0.05
    return True


# ------------------------------------------------------- candidate records

def _build_candidate_records(photos: list, thumb_dir: Path, make_thumbs: bool) -> list[dict]:
    """Turn PhotoInfo objects into JSON-friendly records + optional thumbnails."""
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise RuntimeError("Pillow is required: pip install Pillow") from exc

    records: list[dict] = []
    for i, p in enumerate(photos):
        thumb_path: Optional[str] = None
        if make_thumbs and p.path and os.path.exists(p.path):
            try:
                out = thumb_dir / f"thumb_{i:04d}.jpg"
                with Image.open(p.path) as img:
                    img = ImageOps.exif_transpose(img).convert("RGB")
                    img.thumbnail((512, 512), Image.LANCZOS)
                    img.save(out, "JPEG", quality=72, optimize=True)
                thumb_path = str(out)
            except Exception:
                pass  # iCloud-only or unreadable → skip thumbnail, keep metadata

        score = None
        try:
            if p.score and p.score.overall is not None:
                score = round(float(p.score.overall), 3)
        except Exception:
            pass

        w, h = getattr(p, "width", None), getattr(p, "height", None)
        if w and h:
            orientation = "landscape" if w > h else "portrait" if h > w else "square"
        else:
            orientation = None

        records.append({
            "uuid": p.uuid,
            "date": p.date.isoformat() if p.date else None,
            "place": p.place.name if getattr(p, "place", None) else None,
            "persons": list(p.persons) if p.persons else [],
            "keywords": list(p.keywords) if p.keywords else [],
            "favorite": bool(getattr(p, "favorite", False)),
            "orientation": orientation,
            "aesthetic_score": score,
            "thumbnail_path": thumb_path,
        })
    return records
