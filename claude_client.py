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

_CURATE_SYSTEM = """\
You are an expert photo editor and slideshow curator. You will be given a set of
candidate photos (thumbnails + metadata) and asked to select the best ones for a
slideshow presentation.

Your work has TWO PHASES — do them in order:

PHASE 1 — EXAMINE ALL PHOTOS
Before selecting anything, review every single photo. For each one note:
  • Composition — framing, rule of thirds, leading lines, horizon level, clutter
  • Exposure — brightness, contrast, shadow/highlight detail, colour accuracy
  • Sharpness — focus on the main subject; motion blur that degrades the image
  • Content relevance — does it clearly relate to the slideshow subject?
  • People — note every photo that contains a person, portrait, or human subject
  • Scene group — each record has a scene_group field (integer or null).
    Photos sharing the same scene_group integer are CONFIRMED near-duplicates:
    burst sequences from Mac Photos, or shots taken within seconds at the same
    GPS location. You MUST select at most ONE photo from each scene_group.

PHASE 2 — SELECT
Apply these rules strictly:
  1. DUPLICATES: For every scene_group, keep ONLY the single best photo
     (sharpest focus + best exposure + strongest composition). Discard the rest.
  2. PEOPLE: The final selection MUST include people/portrait shots if any exist
     in the candidates. Human subjects add warmth and story to slideshows.
  3. QUALITY: Among non-duplicate candidates, rank by technical quality:
     sharpness, correct exposure, clean composition.
  4. VARIETY IN ORDER: Arrange so no two consecutive photos share the same
     scene_group, location, or visual theme. Interleave landscapes, people,
     details, and action shots.
  5. NARRATIVE FLOW: wide/establishing → details → people → action →
     atmosphere → strong closing shot.
  6. COUNT: Aim for the target count; return fewer only if not enough
     high-quality, non-duplicate candidates exist.

Return ONLY a JSON object — no prose, no markdown fences:
{"selected_uuids": [string], "rationale": string}

selected_uuids must be in intended slideshow playback order.
"""


def curate_photos(
    prompt: str,
    candidates: list[dict],
    target_count: int,
    use_vision: bool = True,
    curation_prompt: str | None = None,
) -> tuple[list[str], str]:
    """
    Returns (ordered_uuids, rationale).

    Each candidate dict should contain:
      uuid, date, place, persons, keywords, favorite, orientation,
      score (aesthetic 0–1 or None), thumbnail_path (str or None)

    curation_prompt: when provided (user-edited natural-language prompt),
      replaces the auto-generated opening context block.
    """
    client = get_client()

    # ---- opening context block ------------------------------------------------
    if curation_prompt:
        # User-supplied natural-language prompt (shown and edited in the dialog).
        opening = (
            f"{curation_prompt}\n\n"
            f"CANDIDATE PHOTOS ({len(candidates)} total — examine every one):"
        )
    else:
        opening = (
            f"SLIDESHOW SUBJECT: {prompt}\n"
            f"PURPOSE: A photo slideshow presentation about \"{prompt}\"\n"
            f"TARGET PHOTO COUNT: {target_count}\n"
            f"TOTAL CANDIDATES TO EXAMINE: {len(candidates)}\n\n"
            f"PHASE 1 — Please examine ALL {len(candidates)} photos below "
            "before making any selection. Each entry shows the photo's "
            "metadata followed by its thumbnail image (when available). "
            "Assess composition, exposure, sharpness, content relevance, "
            "and flag near-duplicates as you go.\n\n"
            "CANDIDATE PHOTOS:"
        )
    content: list[dict] = [{"type": "text", "text": opening}]

    # ---- one entry per candidate: metadata + thumbnail -------------------------
    for i, c in enumerate(candidates):
        meta = {k: v for k, v in c.items() if k != "thumbnail_path"}
        content.append({
            "type": "text",
            "text": (
                f"\n--- Photo #{i + 1} of {len(candidates)} "
                f"| uuid: {c['uuid']} ---\n"
                f"{json.dumps(meta, default=str)}"
            ),
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

    # ---- PHASE 2 instruction ---------------------------------------------------
    content.append({
        "type": "text",
        "text": (
            f"\n\nPHASE 2 — SELECTION\n"
            f"You have now examined all {len(candidates)} candidate photos. "
            f"Select the best {target_count} for a slideshow presentation "
            f"about \"{prompt}\".\n\n"
            "Apply these criteria in order:\n"
            "1. Eliminate near-duplicates — keep only the single best of each "
            "similar group (best composition, sharpest focus, best exposure).\n"
            "2. Prioritise technical quality: sharp focus on subject, correct "
            "exposure (not blown out or crushed), strong composition.\n"
            "3. Match the slideshow subject and user intent.\n"
            "4. Arrange for narrative flow and variety — no two consecutive "
            "photos should look nearly identical.\n\n"
            f"Return exactly {target_count} UUIDs (or fewer if fewer strong "
            "matches exist), in intended playback order.\n"
            "Return ONLY JSON: "
            '{"selected_uuids": [\"uuid1\", \"uuid2\", ...], '
            '"rationale": \"brief explanation\"}'
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
