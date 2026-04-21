#!/usr/bin/env python3
"""
Test suite for photo_slideshow components.

Covers:
  - _DatePicker widget (state, default date, display)
  - _parse_json_loose robustness (trailing text, fences)
  - Pipeline scan limit modes (date range, count, random)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
