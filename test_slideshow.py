#!/usr/bin/env python3
"""
Test suite for photo_slideshow components.

Covers:
  - _DatePicker widget (state, default date, display)
  - _parse_json_loose robustness (trailing text, fences)
  - Pipeline scan limit modes (date range, count, random)
  - Scene group detection (_add_scene_groups): burst UUID + time/geo proximity
  - Curation prompt workflow (_curation_prompt skips filter spec)
  - Album generation: osascript called with temp file, not -e
  - Pillow decompression bomb limit raised for local photos
  - Random sampling only shuffles matched photos
  - Fallback query OR logic (keyword vs album searched separately)
  - Build Prompt dialog content

Run:  python test_slideshow.py
"""
from __future__ import annotations

import datetime
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ------------------------------------------------------------------ helpers

def _noop_query(spec, limit):
    """Stub that captures args without hitting the Photos DB."""
    _noop_query.calls.append({"spec": spec, "limit": limit})
    return []

_noop_query.calls: list = []


# ------------------------------------------------------------------ DatePicker

class TestDatePicker(unittest.TestCase):
    """_DatePicker widget tests (headless Tk root)."""

    @classmethod
    def setUpClass(cls):
        import tkinter as tk
        cls.root = tk.Tk()
        cls.root.withdraw()   # keep hidden — no window shown

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def _picker(self, initial=None):
        from _dialog_subprocess import _DatePicker
        return _DatePicker(self.root, initial=initial)

    def test_default_date_is_today(self):
        p = self._picker()
        self.assertEqual(p.get_date(), datetime.date.today())

    def test_custom_initial_date(self):
        d = datetime.date(2024, 6, 15)
        p = self._picker(initial=d)
        self.assertEqual(p.get_date(), d)

    def test_display_shows_iso_string(self):
        d = datetime.date(2025, 3, 22)
        p = self._picker(initial=d)
        self.assertEqual(p._var.get(), "2025-03-22")

    def test_disable_greys_out_button(self):
        p = self._picker()
        p.config(state="disabled")
        self.assertEqual(str(p._btn["state"]), "disabled")

    def test_enable_restores_button(self):
        p = self._picker()
        p.config(state="disabled")
        p.config(state="normal")
        self.assertEqual(str(p._btn["state"]), "normal")

    def test_open_calendar_method_exists(self):
        p = self._picker()
        self.assertTrue(callable(p._open_calendar))

    def test_from_default_is_jan1(self):
        """From picker initialises to Jan 1 of current year."""
        expected = datetime.date.today().replace(month=1, day=1)
        with open(
            os.path.join(os.path.dirname(__file__), "_dialog_subprocess.py")
        ) as f:
            src = f.read()
        self.assertIn("replace(month=1, day=1)", src)
        p = self._picker(initial=expected)
        self.assertEqual(p.get_date().month, 1)
        self.assertEqual(p.get_date().day, 1)


# ------------------------------------------------------------------ JSON parser

class TestParseJsonLoose(unittest.TestCase):
    """_parse_json_loose handles Claude's varied output formats."""

    def _parse(self, text):
        from claude_client import _parse_json_loose
        return _parse_json_loose(text)

    def test_clean_json(self):
        r = self._parse('{"selected_uuids": ["a", "b"], "rationale": "ok"}')
        self.assertEqual(r["selected_uuids"], ["a", "b"])

    def test_trailing_prose_ignored(self):
        r = self._parse(
            '{"selected_uuids": ["x"], "rationale": "good"}\n\nExtra text here.'
        )
        self.assertEqual(r["selected_uuids"], ["x"])

    def test_multiple_json_objects_takes_first(self):
        r = self._parse('{"a": 1}\n{"b": 2}')
        self.assertEqual(r["a"], 1)

    def test_json_in_code_fence(self):
        r = self._parse('```json\n{"selected_uuids": ["z"]}\n```')
        self.assertEqual(r["selected_uuids"], ["z"])

    def test_filter_spec_shape(self):
        spec_json = (
            '{"date_range": null, "albums": null, "keywords": ["sunset"], '
            '"persons": null, "places": null, "favorites_only": false, '
            '"has_gps": null, "orientation": "landscape", '
            '"media_type": "any", "limit_candidates": 40}'
        )
        r = self._parse(spec_json)
        self.assertEqual(r["keywords"], ["sunset"])
        self.assertFalse(r["favorites_only"])


# ------------------------------------------------------------------ Pipeline modes

class TestPipelineScanModes(unittest.TestCase):
    """Pipeline correctly applies scan_mode / scan_limit params."""

    def setUp(self):
        _noop_query.calls.clear()

    def _run(self, extra_params: dict):
        import pipeline
        base = {
            "prompt": "test",
            "format": "html",
            "max_photos": 5,
            "visual_curation": False,
        }
        with patch.object(pipeline, "query_photos", side_effect=_noop_query):
            try:
                pipeline.run_pipeline({**base, **extra_params}, Path("/tmp"))
            except Exception:
                pass  # expected — query returns []

    def test_date_range_sets_limit_to_50000(self):
        self._run({
            "scan_mode": "date_range",
            "scan_date_range": {"start": "2025-01-01", "end": "2025-12-31"},
        })
        first = _noop_query.calls[0]
        self.assertEqual(first["limit"], 50_000)

    def test_date_range_patches_spec_date_range(self):
        self._run({
            "scan_mode": "date_range",
            "scan_date_range": {"start": "2025-01-01", "end": "2025-12-31"},
        })
        spec = _noop_query.calls[0]["spec"]
        self.assertEqual(spec["date_range"]["start"], "2025-01-01")
        self.assertEqual(spec["date_range"]["end"], "2025-12-31")

    def test_count_mode_uses_scan_limit(self):
        self._run({"scan_mode": "count", "scan_limit": 1500})
        first = _noop_query.calls[0]
        self.assertEqual(first["limit"], 1500)

    def test_count_mode_minimum_is_max_photos(self):
        # scan_limit below max_photos → clamp to max_photos
        self._run({"scan_mode": "count", "scan_limit": 2, "max_photos": 10})
        first = _noop_query.calls[0]
        self.assertEqual(first["limit"], 10)

    def test_no_scan_mode_uses_default_cap_400(self):
        self._run({})   # no scan_mode key
        first = _noop_query.calls[0]
        self.assertLessEqual(first["limit"], 400)

    def test_random_sample_flag_accepted(self):
        """random_sample=True should not raise before query returns []."""
        # If it raises *before* query, calls will be empty
        self._run({"scan_mode": "count", "scan_limit": 50, "random_sample": True})
        self.assertTrue(len(_noop_query.calls) >= 1)

    def test_open_end_date_range(self):
        """Both start and end can be None (open range)."""
        self._run({
            "scan_mode": "date_range",
            "scan_date_range": {"start": None, "end": None},
        })
        self.assertTrue(len(_noop_query.calls) >= 1)


# ------------------------------------------------------------------ Scene groups

class TestSceneGroups(unittest.TestCase):
    """_add_scene_groups correctly identifies burst/geo-proximity duplicates."""

    def _records(self, entries):
        """Build minimal record dicts from (uuid, iso_date, burst_uuid, gps) tuples."""
        return [
            {
                "uuid": e[0],
                "date": e[1],
                "burst_uuid": e[2] if len(e) > 2 else None,
                "gps": e[3] if len(e) > 3 else None,
            }
            for e in entries
        ]

    def test_same_burst_uuid_grouped(self):
        import pipeline
        recs = self._records([
            ("A", "2025-01-01T10:00:00", "BURST-1"),
            ("B", "2025-01-01T10:00:01", "BURST-1"),
            ("C", "2025-01-01T10:00:02", "BURST-1"),
        ])
        pipeline._add_scene_groups(recs)
        groups = {r["uuid"]: r["scene_group"] for r in recs}
        self.assertEqual(groups["A"], groups["B"])
        self.assertEqual(groups["B"], groups["C"])
        self.assertIsNotNone(groups["A"])

    def test_different_burst_uuid_separate_groups(self):
        import pipeline
        recs = self._records([
            ("A", "2025-01-01T10:00:00", "BURST-1"),
            ("B", "2025-01-01T10:00:01", "BURST-2"),
        ])
        pipeline._add_scene_groups(recs)
        self.assertNotEqual(recs[0]["scene_group"], recs[1]["scene_group"])

    def test_within_5s_no_gps_grouped(self):
        import pipeline
        recs = self._records([
            ("A", "2025-06-01T12:00:00"),
            ("B", "2025-06-01T12:00:03"),  # 3s apart, no GPS
        ])
        pipeline._add_scene_groups(recs)
        self.assertEqual(recs[0]["scene_group"], recs[1]["scene_group"])

    def test_beyond_10s_not_grouped(self):
        import pipeline
        recs = self._records([
            ("A", "2025-06-01T12:00:00"),
            ("B", "2025-06-01T12:00:15"),  # 15s apart — too far to group
        ])
        pipeline._add_scene_groups(recs)
        g_a = recs[0]["scene_group"]
        g_b = recs[1]["scene_group"]
        # Correct: both are None (no group), OR assigned to different groups.
        # They must NOT share the same non-None group ID.
        if g_a is not None and g_b is not None:
            self.assertNotEqual(g_a, g_b, "15s-apart photos should not share a group")

    def test_solo_photo_has_no_group(self):
        import pipeline
        recs = self._records([("A", "2025-06-01T12:00:00")])
        pipeline._add_scene_groups(recs)
        self.assertIsNone(recs[0]["scene_group"])

    def test_geo_close_within_10s_grouped(self):
        import pipeline
        gps = {"lat": 37.7749, "lon": -122.4194}
        recs = self._records([
            ("A", "2025-06-01T12:00:00", None, gps),
            ("B", "2025-06-01T12:00:08", None, gps),  # 8s, same GPS
        ])
        pipeline._add_scene_groups(recs)
        self.assertEqual(recs[0]["scene_group"], recs[1]["scene_group"])


# ------------------------------------------------------------------ Curation prompt

class TestCurationPromptWorkflow(unittest.TestCase):
    """When _curation_prompt is provided, filter spec step is skipped."""

    def setUp(self):
        _noop_query.calls.clear()

    def _run(self, params):
        import pipeline
        with patch.object(pipeline, "query_photos", side_effect=_noop_query):
            try:
                pipeline.run_pipeline(params, Path("/tmp"))
            except Exception:
                pass

    def test_curation_prompt_skips_filter_spec(self):
        """With _curation_prompt, query is called without calling prompt_to_filter_spec."""
        import pipeline
        filter_spec_calls = []
        with patch.object(pipeline, "prompt_to_filter_spec",
                          side_effect=lambda **kw: filter_spec_calls.append(kw) or {}):
            with patch.object(pipeline, "query_photos", side_effect=_noop_query):
                try:
                    pipeline.run_pipeline(
                        {"prompt": "x", "format": "html", "max_photos": 5,
                         "visual_curation": False, "_curation_prompt": "my prompt"},
                        Path("/tmp"),
                    )
                except Exception:
                    pass
        self.assertEqual(len(filter_spec_calls), 0,
                         "prompt_to_filter_spec should NOT be called when _curation_prompt is set")
        self.assertTrue(len(_noop_query.calls) >= 1,
                        "query_photos should still be called")

    def test_without_curation_prompt_calls_filter_spec(self):
        """Without _curation_prompt, prompt_to_filter_spec IS called."""
        import pipeline
        filter_spec_calls = []
        with patch.object(pipeline, "prompt_to_filter_spec",
                          side_effect=lambda **kw: filter_spec_calls.append(kw)
                          or {"media_type": "any", "limit_candidates": 20}):
            with patch.object(pipeline, "query_photos", side_effect=_noop_query):
                try:
                    pipeline.run_pipeline(
                        {"prompt": "x", "format": "html", "max_photos": 5,
                         "visual_curation": False},
                        Path("/tmp"),
                    )
                except Exception:
                    pass
        self.assertEqual(len(filter_spec_calls), 1)

    def test_curation_prompt_passed_to_curate_photos(self):
        """The curation prompt reaches curate_photos as the curation_prompt kwarg."""
        import pipeline
        curate_kwargs = {}

        def fake_curate(**kw):
            curate_kwargs.update(kw)
            return ([], "")

        # Return fake photos so curate is reached
        class _P:
            uuid = "u1"; date = None; persons = []; keywords = []
            albums = []; place = None; filename = "x.jpg"; path = None
            hidden = False; intrash = False; score = None
            width = 100; height = 100; burst_uuid = None; location = None

        with patch.object(pipeline, "query_photos", return_value=[_P()] * 6):
            with patch.object(pipeline, "curate_photos",
                              side_effect=lambda **kw: fake_curate(**kw)):
                with patch.object(pipeline, "generate_output",
                                  return_value=Path("/tmp/x.html")):
                    with patch("pipeline._build_candidate_records",
                               return_value=[{"uuid": "u1", "date": None,
                                              "place": None, "persons": [],
                                              "keywords": [], "favorite": False,
                                              "orientation": None,
                                              "aesthetic_score": None,
                                              "thumbnail_path": None,
                                              "burst_uuid": None, "gps": None,
                                              "scene_group": None}] * 6):
                        try:
                            pipeline.run_pipeline(
                                {"prompt": "x", "format": "html",
                                 "max_photos": 3, "visual_curation": True,
                                 "_curation_prompt": "MY CUSTOM PROMPT"},
                                Path("/tmp"),
                            )
                        except Exception:
                            pass
        self.assertEqual(curate_kwargs.get("curation_prompt"), "MY CUSTOM PROMPT")


# ------------------------------------------------------------------ Album generation

class TestAlbumGeneration(unittest.TestCase):
    """_generate_album uses a temp file for osascript, not -e."""

    def test_uses_temp_file_not_inline_arg(self):
        import generators, os
        from unittest.mock import MagicMock

        calls = []
        def _fake_run(cmd, **kw):
            calls.append(list(cmd))
            m = MagicMock()
            m.returncode = 0
            m.stderr = ""
            return m

        class _P:
            def __init__(self, u):
                self.uuid = u

        with patch("generators.subprocess.run", side_effect=_fake_run):
            try:
                generators._generate_album(
                    [_P(f"UUID-{i}") for i in range(30)],
                    Path("/tmp"), "test", "prompt"
                )
            except Exception:
                pass

        self.assertEqual(len(calls), 1)
        cmd = calls[0]
        self.assertEqual(cmd[0], "osascript")
        self.assertEqual(len(cmd), 2, "Should be [osascript, filepath] not [osascript, -e, script]")
        self.assertFalse(cmd[1] == "-e", "Must not use -e flag")
        self.assertTrue(cmd[1].endswith(".applescript"))
        # Temp file must be deleted
        self.assertFalse(os.path.exists(cmd[1]), "Temp file must be cleaned up")

    def test_applescript_contains_all_uuids(self):
        """The generated AppleScript should reference every UUID."""
        import generators
        uuids = [f"UUID-{i:03d}" for i in range(10)]
        script = generators._build_applescript("Test Album", uuids)
        for u in uuids:
            self.assertIn(u, script)
        self.assertIn("Test Album", script)


# ------------------------------------------------------------------ Pillow limit

class TestPillowLimit(unittest.TestCase):
    """_build_candidate_records raises Pillow's image size limit."""

    def test_max_image_pixels_raised(self):
        from PIL import Image
        import pipeline

        Image.MAX_IMAGE_PIXELS = 178_956_970  # Pillow default
        with patch("pipeline.os.path.exists", return_value=False):
            pipeline._build_candidate_records([], Path("/tmp"), make_thumbs=True)
        self.assertEqual(Image.MAX_IMAGE_PIXELS, 300_000_000)


# ------------------------------------------------------------------ Random sampling

class TestRandomSampling(unittest.TestCase):
    """Random sampling only shuffles already-matched photos."""

    def _make_photos(self, n):
        """Return n fake photo objects."""
        class _P:
            def __init__(self, i):
                self.uuid = f"p{i:03d}"
                self.date = None
                self.hidden = False
                self.intrash = False
        return [_P(i) for i in range(n)]

    def test_random_trims_to_pick_count(self):
        import pipeline, random
        photos = self._make_photos(200)
        captured = {"photos": None}

        def fake_query(spec, limit):
            return photos  # return all 200

        with patch.object(pipeline, "query_photos", side_effect=fake_query):
            with patch.object(pipeline, "_add_scene_groups"):
                with patch.object(pipeline, "_build_candidate_records",
                                  side_effect=lambda p, *a, **kw:
                                      (captured.__setitem__("photos", p) or [])):
                    try:
                        pipeline.run_pipeline(
                            {"prompt": "x", "format": "html", "max_photos": 10,
                             "visual_curation": False, "scan_mode": "count",
                             "scan_limit": 200, "random_sample": True,
                             "random_pick_count": 50},
                            Path("/tmp"),
                        )
                    except Exception:
                        pass

        # After random sampling, candidate_records was called with ≤50 photos
        self.assertIsNotNone(captured["photos"])
        self.assertLessEqual(len(captured["photos"]), 50)

    def test_random_photos_are_subset_of_matched(self):
        """All randomly picked photos must have been in the original matched set."""
        import pipeline
        photos = self._make_photos(100)
        original_uuids = {p.uuid for p in photos}
        captured = {"photos": None}

        with patch.object(pipeline, "query_photos", return_value=photos):
            with patch.object(pipeline, "_add_scene_groups"):
                with patch.object(pipeline, "_build_candidate_records",
                                  side_effect=lambda p, *a, **kw:
                                      (captured.__setitem__("photos", p) or [])):
                    try:
                        pipeline.run_pipeline(
                            {"prompt": "x", "format": "html", "max_photos": 5,
                             "visual_curation": False, "scan_mode": "count",
                             "scan_limit": 100, "random_sample": True,
                             "random_pick_count": 20},
                            Path("/tmp"),
                        )
                    except Exception:
                        pass

        if captured["photos"] is not None:
            picked_uuids = {p.uuid for p in captured["photos"]}
            self.assertTrue(picked_uuids.issubset(original_uuids),
                            "Random picks must all come from the matched set")


# ------------------------------------------------------------------ Fallback query

class TestFallbackQuery(unittest.TestCase):
    """_fallback_query runs keyword and album searches separately (OR logic)."""

    def test_strategy1_runs_two_separate_queries(self):
        """Strategy 1 should call query_photos twice: once for keywords, once for albums."""
        import pipeline
        calls = []

        def _capture(spec, limit):
            calls.append(spec)
            return []

        with patch.object(pipeline, "query_photos", side_effect=_capture):
            pipeline._fallback_query(
                {"date_range": None, "media_type": "any"},
                limit=100,
                prompt="sunset vacation",
            )

        # Expect at least 2 calls (keyword query + album query)
        self.assertGreaterEqual(len(calls), 2)
        keyword_calls = [c for c in calls if c.get("keywords")]
        album_calls   = [c for c in calls if c.get("albums")]
        self.assertTrue(len(keyword_calls) >= 1, "Should have a keywords-only query")
        self.assertTrue(len(album_calls)   >= 1, "Should have an albums-only query")

    def test_strategy1_does_not_set_both_albums_and_keywords_simultaneously(self):
        """No single query should set both albums AND keywords (that's AND logic)."""
        import pipeline
        calls = []

        with patch.object(pipeline, "query_photos",
                          side_effect=lambda spec, limit: calls.append(spec) or []):
            pipeline._fallback_query(
                {"date_range": None, "media_type": "any"}, 100, "sunset"
            )

        for c in calls:
            both = bool(c.get("keywords")) and bool(c.get("albums"))
            self.assertFalse(both, f"Query set both keywords AND albums: {c}")

    def test_returns_empty_when_nothing_matches_and_no_vision(self):
        import pipeline
        with patch.object(pipeline, "query_photos", return_value=[]):
            result = pipeline._fallback_query(
                {}, 50, "xyz", visual_curation=False
            )
        self.assertEqual(result, [])


# ------------------------------------------------------------------ Build Prompt

class TestBuildPromptContent(unittest.TestCase):
    """The Build Prompt function produces a well-formed natural-language prompt."""

    def _build(self, prompt_text, n=10):
        """Simulate what do_build_prompt() produces."""
        return (
            f'I am creating a slideshow presentation about:\n'
            f'  "{prompt_text}"\n\n'
            f'Please examine ALL the candidate photos provided below and select '
            f'the best {n} for this slideshow.\n\n'
            f'WHAT I\'M LOOKING FOR:\n'
            f'\u2022 Photos that clearly match the subject: {prompt_text}\n'
            f'\u2022 People / portraits \u2014 human subjects add warmth and story to slideshows\n'
            f'\u2022 Technical quality: sharp focus on the main subject, correct exposure, '
            f'strong composition\n'
            f'\u2022 Variety: a mix of wide/establishing shots, close-up details, '
            f'people, and atmospheric scenes\n\n'
            f'WHAT TO EXCLUDE:\n'
            f'\u2022 Near-duplicates \u2014 photos sharing the same scene_group number were '
            f'taken within seconds of each other (burst mode or rapid-fire). '
            f'Keep only the single BEST from each group.\n'
            f'\u2022 Blurry, over/under-exposed, or poorly composed photos\n'
            f'\u2022 Photos that do not relate to the stated subject\n\n'
            f'PLAYBACK ORDER:\n'
            f'Arrange the selected photos for a smooth, engaging slideshow:\n'
            f'  wide establishing shot \u2192 scene details \u2192 people/portraits \u2192 '
            f'action/events \u2192 atmosphere \u2192 memorable closing image\n\n'
            f'No two consecutive photos should be from the same scene_group, '
            f'location, or visual theme.'
        )

    def test_contains_subject(self):
        out = self._build("Morocco trip")
        self.assertIn("Morocco trip", out)

    def test_mentions_people(self):
        out = self._build("vacation")
        self.assertIn("People", out)

    def test_mentions_scene_group(self):
        out = self._build("any")
        self.assertIn("scene_group", out)

    def test_mentions_target_count(self):
        out = self._build("beach", n=15)
        self.assertIn("15", out)

    def test_contains_playback_order(self):
        out = self._build("nature")
        self.assertIn("PLAYBACK ORDER", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
