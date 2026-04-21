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


# ------------------------------------------------------------------ date picker

class _DatePicker(ttk.Frame):
    """A date field + 📅 button that opens a modal Toplevel calendar picker.
    Uses a Toplevel (not a dropdown) so it works reliably on macOS.
    """
    def __init__(self, parent, initial: datetime.date | None = None, **kwargs):
        super().__init__(parent, **kwargs)
        self._date = initial or datetime.date.today()
        self._var = tk.StringVar(value=self._date.isoformat())
        self._entry = ttk.Entry(self, textvariable=self._var,
                                width=11, state="readonly")
        self._entry.pack(side="left")
        self._btn = ttk.Button(self, text="\U0001f4c5", width=2,
                               command=self._open_calendar)
        self._btn.pack(side="left", padx=(2, 0))

    def get_date(self) -> datetime.date:
        return self._date

    def config(self, **kw):  # type: ignore[override]
        state = kw.pop("state", None)
        super().config(**kw)
        if state is not None:
            # readonly keeps text readable; disabled greys it out
            entry_state = "readonly" if state == "normal" else "disabled"
            self._entry.config(state=entry_state)
            self._btn.config(state=state)

    def _open_calendar(self) -> None:
        try:
            from tkcalendar import Calendar
        except ImportError:
            return

        top = tk.Toplevel(self.winfo_toplevel())
        top.title("Select date")
        top.resizable(False, False)
        top.grab_set()          # modal
        top.attributes("-topmost", True)
        top.transient(self.winfo_toplevel())

        cal = Calendar(
            top, selectmode="day",
            year=self._date.year,
            month=self._date.month,
            day=self._date.day,
            date_pattern="yyyy-mm-dd",
        )
        cal.pack(padx=12, pady=12)

        def _ok() -> None:
            self._date = cal.selection_get()
            self._var.set(self._date.isoformat())
            top.destroy()

        btns = ttk.Frame(top)
        btns.pack(pady=(0, 10))
        ttk.Button(btns, text="OK", command=_ok, width=8).pack(
            side="left", padx=6)
        ttk.Button(btns, text="Cancel", command=top.destroy, width=8).pack(
            side="left", padx=6)

        # Centre the popup over the main window
        top.update_idletasks()
        root = self.winfo_toplevel()
        x = root.winfo_x() + (root.winfo_width() - top.winfo_width()) // 2
        y = root.winfo_y() + (root.winfo_height() - top.winfo_height()) // 2
        top.geometry(f"+{x}+{y}")
        top.wait_window()


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

    # ── SCAN LIMIT ────────────────────────────────────────────────────────────
    scan_frame = ttk.LabelFrame(outer, text="Scan Limit", padding=8)
    scan_frame.pack(fill="x", pady=(10, 0))
    scan_frame.columnconfigure(1, weight=1)

    scan_mode_var = tk.StringVar(value="count")

    # Row 0: date range radio + _DatePicker widgets
    ttk.Radiobutton(
        scan_frame, text="Limit by date range",
        variable=scan_mode_var, value="date_range",
        command=lambda: _toggle_scan_mode(),
    ).grid(row=0, column=0, sticky="w", pady=4)

    date_row = ttk.Frame(scan_frame)
    date_row.grid(row=0, column=1, sticky="w", pady=4, padx=(8, 0))

    # "From" end — optional checkbox + calendar button picker
    use_start_var = tk.BooleanVar(value=False)
    use_start_chk = ttk.Checkbutton(date_row, text="From:", variable=use_start_var,
                                    command=lambda: _toggle_date_ends())
    use_start_chk.pack(side="left")
    scan_start_picker = _DatePicker(
        date_row,
        initial=datetime.date.today().replace(month=1, day=1),  # Jan 1 this year
    )
    scan_start_picker.pack(side="left", padx=(4, 12))
    scan_start_picker.config(state="disabled")

    # "To" end — optional checkbox + calendar button picker
    use_end_var = tk.BooleanVar(value=True)
    use_end_chk = ttk.Checkbutton(date_row, text="To:", variable=use_end_var,
                                  command=lambda: _toggle_date_ends())
    use_end_chk.pack(side="left")
    scan_end_picker = _DatePicker(date_row, initial=datetime.date.today())
    scan_end_picker.pack(side="left", padx=(4, 0))

    ttk.Label(date_row, text="(uncheck to leave that end open)",
              foreground="#888").pack(side="left", padx=(10, 0))

    def _toggle_date_ends() -> None:
        scan_start_picker.config(state="normal" if use_start_var.get() else "disabled")
        scan_end_picker.config(state="normal" if use_end_var.get() else "disabled")

    # Row 1: photo count radio + spinbox
    ttk.Radiobutton(
        scan_frame, text="Limit by number of photos to scan",
        variable=scan_mode_var, value="count",
        command=lambda: _toggle_scan_mode(),
    ).grid(row=1, column=0, sticky="w", pady=4)

    count_row = ttk.Frame(scan_frame)
    count_row.grid(row=1, column=1, sticky="w", pady=4, padx=(8, 0))
    scan_count_var = tk.IntVar(value=400)
    scan_count_spin = ttk.Spinbox(count_row, from_=10, to=50000,
                                  textvariable=scan_count_var, width=8)
    scan_count_spin.pack(side="left")
    ttk.Label(count_row, text="photos from library", foreground="#888").pack(
        side="left", padx=(8, 0)
    )
    random_var = tk.BooleanVar(value=False)
    random_check = ttk.Checkbutton(
        count_row, text="Random",
        variable=random_var,
    )
    random_check.pack(side="left", padx=(12, 0))

    def _toggle_scan_mode() -> None:
        mode = scan_mode_var.get()
        is_date = mode == "date_range"
        count_state = "normal" if not is_date else "disabled"
        # Date-end widgets follow their own checkboxes when date mode is active.
        chk_state = "normal" if is_date else "disabled"
        use_start_chk.config(state=chk_state)
        use_end_chk.config(state=chk_state)
        if is_date:
            _toggle_date_ends()
        else:
            scan_start_picker.config(state="disabled")
            scan_end_picker.config(state="disabled")
        scan_count_spin.config(state=count_state)
        random_check.config(state=count_state)

    _toggle_scan_mode()  # set initial state

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
            "scan_mode": scan_mode_var.get(),
        }
        if filter_spec is not None:
            params["_filter_spec"] = filter_spec
        if scan_mode_var.get() == "date_range":
            params["scan_date_range"] = {
                "start": scan_start_picker.get_date().isoformat()
                         if use_start_var.get() else None,
                "end":   scan_end_picker.get_date().isoformat()
                         if use_end_var.get() else None,
            }
        else:
            params["scan_limit"] = int(scan_count_var.get())
            params["random_sample"] = bool(random_var.get())

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
