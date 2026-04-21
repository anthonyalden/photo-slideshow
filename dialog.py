"""
Slideshow request dialog — launches _dialog_subprocess.py in a subprocess.

Running Tkinter in a subprocess avoids the rumps/Cocoa event-loop conflict
(both frameworks fight for the main thread when used in the same process).
The subprocess prints a JSON object to stdout; we read it back here.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Optional


_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_dialog_subprocess.py")


def prompt_user() -> Optional[dict]:
    """Open the slideshow dialog in a subprocess and return params or None."""
    try:
        r = subprocess.run(
            [sys.executable, _SCRIPT],
            capture_output=True,
            text=True,
            env=os.environ.copy(),  # pass ANTHROPIC_API_KEY through
        )
    except Exception:
        return None

    out = r.stdout.strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None
