"""
Claude API integration.

Two calls per slideshow:
  1. prompt_to_filter_spec  — Sonnet, text-only, cheap. NL → JSON filter.
  2. curate_photos          — Opus with vision (if enabled), re-ranks candidates
                              and chooses slideshow order.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Optional

from anthropic import Anthropic


FILTER_MODEL = "claude-sonnet-4-6"
CURATE_MODEL_VISION = "claude-opus-4-7"
CURATE_MODEL_TEXT = "claude-sonnet-4-6"


_client: Optional[Anthropic] = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set.\n\n"
                "Add it to your shell profile (e.g. ~/.zshrc):\n"
                "  export ANTHROPIC_API_KEY=sk-ant-..."
            )
        _client = Anthropic(api_key=api_key)
    return _client


# --------------------------------------------------------------- Stage 1: filter

_FILTER_SYSTEM = """You translate a user's natural-language description of photos into a JSON filter spec that can be applied to a macOS Photos library via osxphotos.

Return ONLY a JSON object, no prose, no markdown fences. Schema:

{
  "date_range": {"start": "YYYY-MM-DD" | null, "end": "YYYY-MM-DD" | null} | null,
  "albums":      [string] | null,   // album-name substrings
  "keywords":    [string] | null,   // tag/keyword matches (any)
  "persons":     [string] | null,   // face-tagged person names
  "places":      [string] | null,   // city/country/POI substrings
  "favorites_only": boolean,
  "has_gps":        boolean | null,
  "orientation":    "landscape" | "portrait" | "square" | null,
  "media_type":     "photo" | "video" | "any",
  "limit_candidates": integer        // pre-curation cap, 3–6× the target output count
}

Rules:
- If the prompt has no date cue, set date_range to null.
- Resolve relative dates ("last summer", "a few months ago") using CURRENT_DATE.
- Do not invent album or person names — only include ones the user explicitly said.
- Vague intent like "best of", "favorites", "highlights" → leave most filters null and rely on curation.
- If the user mentions "favorites" explicitly, set favorites_only: true.
- limit_candidates defaults to target_count × 4, capped at 400."""


def prompt_to_filter_spec(prompt: str, target_count: int, current_date: str) -> dict:
    client = get_client()
    msg = client.messages.create(
        model=FILTER_MODEL,
        max_tokens=1024,
        system=_FILTER_SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"CURRENT_DATE: {current_date}\n"
                f"TARGET_OUTPUT_COUNT: {target_count}\n"
                f"USER_PROMPT: {prompt}\n\n"
                "Produce the filter JSON."
            ),
        }],
    )
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    return _parse_json_loose(text)


# --------------------------------------------------------------- Stage 2: curate

_CURATE_SYSTEM = """You curate photo slideshows.

Given the user's intent and a set of candidate photos (metadata and, if provided, thumbnails), select the best ones and order them for a slideshow with good narrative flow.

Guidelines:
- Match the user's stated intent first; quality and variety second.
- Avoid near-duplicates (same scene, same second, similar composition).
- Prefer a rhythm: establishing shot → detail → people → scene → detail → closer.
- Keep people-heavy and landscape-heavy shots balanced unless intent says otherwise.
- Respect the target count. If fewer strong matches exist, return fewer.

Return ONLY a JSON object, no prose, no fences:
{"selected_uuids": [string], "rationale": string}

selected_uuids must be in playback order."""


def curate_photos(
    prompt: str,
    candidates: list[dict],
    target_count: int,
    use_vision: bool = True,
) -> tuple[list[str], str]:
    """
    Returns (ordered_uuids, rationale).

    Each candidate dict should contain:
      uuid, date, place, persons, keywords, favorite, orientation,
      score (aesthetic 0–1 or None), thumbnail_path (str or None)
    """
    client = get_client()

    content: list[dict] = [{
        "type": "text",
        "text": (
            f"USER_PROMPT: {prompt}\n"
            f"TARGET_COUNT: {target_count}\n"
            f"CANDIDATES: {len(candidates)} total, listed below.\n"
        ),
    }]

    for i, c in enumerate(candidates):
        meta = {k: v for k, v in c.items() if k != "thumbnail_path"}
        content.append({
            "type": "text",
            "text": f"--- #{i} uuid={c['uuid']} ---\n{json.dumps(meta, default=str)}",
        })
        if use_vision and c.get("thumbnail_path"):
            try:
                img_bytes = Path(c["thumbnail_path"]).read_bytes()
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.standard_b64encode(img_bytes).decode(),
                    },
                })
            except Exception:
                pass

    content.append({
        "type": "text",
        "text": (
            f"\nSelect up to {target_count} in playback order. "
            "Return JSON only."
        ),
    })

    model = CURATE_MODEL_VISION if use_vision else CURATE_MODEL_TEXT
    msg = client.messages.create(
        model=model,
        max_tokens=4096,
        system=_CURATE_SYSTEM,
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    data = _parse_json_loose(text)
    uuids = data.get("selected_uuids", [])[:target_count]
    rationale = data.get("rationale", "")
    return uuids, rationale


# --------------------------------------------------------------- helpers

def _parse_json_loose(text: str) -> dict:
    """Parse JSON that may be wrapped in ```json fences or followed by extra text."""
    t = text.strip()

    # Strip ```json ... ``` fences if present.
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:].lstrip("\n")
        t = t.rstrip("`").strip()

    # Use raw_decode so any trailing prose after the JSON object is ignored.
    try:
        obj, _ = json.JSONDecoder().raw_decode(t)
        return obj
    except json.JSONDecodeError:
        # Last resort: find the first { ... } block and parse that.
        start = t.find("{")
        end = t.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(t[start : end + 1])
        raise
