#!/usr/bin/env python3
"""
Standalone Tkinter dialog for Photo Slideshow.

Launched as a subprocess by dialog.py to avoid the rumps/Cocoa event-loop
conflict. Prints a single JSON object to stdout when the user clicks Generate,
or exits with nothing if cancelled.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import threading
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# Allow importing claude_client from the same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FORMATS = [
    ("MP4 Video (ffmpeg)", "mp4"),
    ("Keynote / PowerPoint (.pptx)", "pptx"),
    ("HTML Slideshow", "html"),
    ("Photos Album (smart)", "album"),
]
FORMAT_LABELS = [f[0] for f in FORMATS]
LABEL_TO_KEY = dict(FORMATS)


# ------------------------------------------------------------------ helpers

def _scrolled_text(parent, height: int, mono: bool = False, **kwargs) -> tk.Text:
    """Text widget with vertical + optional horizontal scrollbars."""
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True)

    yscroll = ttk.Scrollbar(frame, orient="vertical")
    xscroll = ttk.Scrollbar(frame, orient="horizontal")

    wrap = "none" if mono else "word"
    fnt = ("Menlo", 11) if mono else ("-size", 12)
    tv = tk.Text(
        frame,
        height=height,
        wrap=wrap,
        font=fnt,
        yscrollcommand=yscroll.set,
        xscrollcommand=xscroll.set,
        relief="sunken",
        borderwidth=1,
        **kwargs,
    )
    yscroll.config(command=tv.yview)
    xscroll.config(command=tv.xview)

    yscroll.pack(side="right", fill="y")
    if mono:
        xscroll.pack(side="bottom", fill="x")
    tv.pack(side="left", fill="both", expand=True)
    return tv


# ------------------------------------------------------------------ main

def main() -> None:
    root = tk.Tk()
    root.title("New Photo Slideshow")
    root.geometry("620x700")
    root.minsize(500, 580)
    root.resizable(True, True)
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass

    result: dict = {}

    # ── outer container ───────────────────────────────────────────────────────
    outer = ttk.Frame(root, padding=16)
    outer.pack(fill="both", expand=True)

    # ── PROMPT ────────────────────────────────────────────────────────────────
    ttk.Label(
        outer,
        text="Describe your slideshow",
        font=("-size", 14, "-weight", "bold"),
    ).pack(anchor="w")
    ttk.Label(
        outer,
        text='e.g.  "our Morocco trip, sunset shots, me and my wife"',
        foreground="#888",
    ).pack(anchor="w", pady=(2, 6))

    prompt_frame = ttk.LabelFrame(outer, text="Prompt", padding=4)
    prompt_frame.pack(fill="both", expand=True)
    prompt_text = _scrolled_text(prompt_frame, height=6)
    prompt_text.focus()

    # ── OPTIONS ───────────────────────────────────────────────────────────────
    opts = ttk.LabelFrame(outer, text="Options", padding=8)
    opts.pack(fill="x", pady=(10, 0))
    opts.columnconfigure(1, weight=1)

    # Format
    ttk.Label(opts, text="Output format:").grid(row=0, column=0, sticky="w", pady=4, padx=(0, 8))
    fmt_var = tk.StringVar(value=FORMAT_LABELS[0])
    ttk.Combobox(
        opts, textvariable=fmt_var, values=FORMAT_LABELS, state="readonly", width=36,
    ).grid(row=0, column=1, sticky="ew", pady=4)

    # Max photos
    ttk.Label(opts, text="Max photos:").grid(row=1, column=0, sticky="w", pady=4, padx=(0, 8))
    max_var = tk.IntVar(value=40)
    ttk.Spinbox(opts, from_=1, to=500, textvariable=max_var, width=8).grid(
        row=1, column=1, sticky="w", pady=4
    )

    # Visual curation
    visual_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(
        opts,
        text="Visual curation  (Claude with vision — slower, better picks)",
        variable=visual_var,
    ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 2))

    # ── SEPARATOR ─────────────────────────────────────────────────────────────
    ttk.Separator(outer, orient="horizontal").pack(fill="x", pady=10)

    # ── FILTER SPEC ───────────────────────────────────────────────────────────
    ttk.Label(
        outer,
        text="Filter Spec sent to Photos  (editable)",
        font=("-size", 13, "-weight", "bold"),
    ).pack(anchor="w")
    ttk.Label(
        outer,
        text="Click \"Preview Filter\" to generate this from your prompt, then edit freely before generating.",
        foreground="#888",
        wraplength=580,
        justify="left",
    ).pack(anchor="w", pady=(2, 6))

    filter_frame = ttk.LabelFrame(outer, text="Filter JSON", padding=4)
    filter_frame.pack(fill="both", expand=True)
    filter_text = _scrolled_text(filter_frame, height=8, mono=True)

    # ── STATUS ────────────────────────────────────────────────────────────────
    status_var = tk.StringVar(
        value='Click "Preview Filter" to inspect the filter, or go straight to Generate.'
    )
    status_lbl = ttk.Label(
        outer, textvariable=status_var, foreground="#555", wraplength=580, justify="left"
    )
    status_lbl.pack(anchor="w", pady=(8, 4))

    # ── BUTTONS ───────────────────────────────────────────────────────────────
    btn_row = ttk.Frame(outer)
    btn_row.pack(fill="x", pady=(4, 0))

    # -- Preview Filter --------------------------------------------------------
    def do_preview() -> None:
        prompt = prompt_text.get("1.0", "end").strip()
        if not prompt:
            status_var.set("⚠️  Enter a prompt first.")
            return
        preview_btn.config(state="disabled")
        generate_btn.config(state="disabled")
        status_var.set("⏳  Calling Claude to generate filter spec…")

        def bg() -> None:
            try:
                from claude_client import prompt_to_filter_spec  # noqa: PLC0415
                spec = prompt_to_filter_spec(
                    prompt=prompt,
                    target_count=int(max_var.get()),
                    current_date=datetime.date.today().isoformat(),
                )
                spec_json = json.dumps(spec, indent=2)
                root.after(0, lambda s=spec_json: _preview_done(s, None))
            except Exception as exc:  # noqa: BLE001
                root.after(0, lambda e=exc: _preview_done(None, e))

        threading.Thread(target=bg, daemon=True).start()

    def _preview_done(spec_json: str | None, error: Exception | None) -> None:
        preview_btn.config(state="normal")
        generate_btn.config(state="normal")
        if error:
            status_var.set(f"⚠️  {error}")
        else:
            filter_text.delete("1.0", "end")
            filter_text.insert("1.0", spec_json)
            status_var.set("✅  Filter spec ready — edit if needed, then Generate.")

    # -- Cancel ----------------------------------------------------------------
    def do_cancel() -> None:
        root.destroy()

    # -- Generate --------------------------------------------------------------
    def do_generate() -> None:
        prompt = prompt_text.get("1.0", "end").strip()
        if not prompt:
            status_var.set("⚠️  Enter a prompt first.")
            return

        filter_spec = None
        raw = filter_text.get("1.0", "end").strip()
        if raw:
            try:
                filter_spec = json.loads(raw)
            except json.JSONDecodeError:
                status_var.set("⚠️  Invalid JSON in filter spec — fix or clear it.")
                return

        params: dict = {
            "prompt": prompt,
            "format": LABEL_TO_KEY.get(fmt_var.get(), "mp4"),
            "max_photos": int(max_var.get()),
            "visual_curation": bool(visual_var.get()),
        }
        if filter_spec is not None:
            params["_filter_spec"] = filter_spec

        result.update(params)
        root.destroy()

    preview_btn = ttk.Button(btn_row, text="Preview Filter", command=do_preview)
    preview_btn.pack(side="left")

    generate_btn = ttk.Button(btn_row, text="Generate", command=do_generate)
    generate_btn.pack(side="right")

    ttk.Button(btn_row, text="Cancel", command=do_cancel).pack(side="right", padx=(0, 8))

    root.bind("<Escape>", lambda _e: do_cancel())
    root.bind("<Command-Return>", lambda _e: do_generate())
    root.bind("<Control-Return>", lambda _e: do_generate())

    root.mainloop()

    if result:
        print(json.dumps(result))


if __name__ == "__main__":
    main()
