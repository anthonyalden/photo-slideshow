"""
Photo Slideshow — macOS menu bar app.

Run with:  python main.py
Stays in the menu bar as 📸. Click "New Slideshow…" to open the dialog.
"""
from __future__ import annotations

import threading
import traceback
import subprocess
from pathlib import Path

import rumps

from dialog import prompt_user
from pipeline import run_pipeline


class PhotoSlideshowApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("📸", quit_button="Quit")
        self.menu = [
            "New Slideshow…",
            "Open Output Folder",
        ]
        self.output_dir = Path.home() / "Pictures" / "PhotoSlideshows"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._busy = False
        self._reset_timer: threading.Timer | None = None

    # ---------------------------------------------------------------- actions

    @rumps.clicked("New Slideshow…")
    def new_slideshow(self, _) -> None:
        if self._busy:
            rumps.alert("Already working on a slideshow. Hold on…")
            return

        params = prompt_user()
        if params is None:
            return  # cancelled

        self._busy = True
        self.title = "⏳"
        threading.Thread(target=self._run, args=(params,), daemon=True).start()

    @rumps.clicked("Open Output Folder")
    def open_output(self, _) -> None:
        subprocess.run(["open", str(self.output_dir)])

    # ---------------------------------------------------------------- worker

    def _run(self, params: dict) -> None:
        try:
            output_path = run_pipeline(
                params,
                self.output_dir,
                on_progress=self._set_title,
            )
            self.title = "✅"
            subprocess.run(["open", "-R", str(output_path)])
            # Also try a notification (works only when bundled as a .app)
            try:
                rumps.notification(
                    title="Slideshow Ready",
                    subtitle=params["prompt"][:60],
                    message=str(output_path.name),
                )
            except Exception:
                pass
        except Exception as exc:
            traceback.print_exc()
            self.title = "⚠️"
            # rumps.alert() must run on the main thread; use osascript instead.
            msg = str(exc)[:200].replace('"', "'")
            subprocess.run([
                "osascript", "-e",
                f'display alert "Slideshow failed" message "{msg}"'
                ' as warning buttons {"OK"} default button "OK"',
            ])
        finally:
            self._busy = False
            self._schedule_reset()

    def _set_title(self, text: str) -> None:
        self.title = text

    def _schedule_reset(self) -> None:
        if self._reset_timer:
            self._reset_timer.cancel()
        self._reset_timer = threading.Timer(5.0, lambda: setattr(self, "title", "📸"))
        self._reset_timer.daemon = True
        self._reset_timer.start()


if __name__ == "__main__":
    PhotoSlideshowApp().run()
