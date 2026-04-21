"""
CLI entry point — use this from macOS Shortcuts, Raycast, Alfred, or directly in Terminal.

Examples:
    python cli.py "sunset shots from our Morocco trip" --format mp4 --max 30
    python cli.py --prompt-file /tmp/prompt.txt --format html
    python cli.py "favorites from last summer" --format album --no-vision
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline import run_pipeline


FORMAT_CHOICES = ["mp4", "pptx", "html", "album"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="photo-slideshow",
        description="Generate a slideshow from macOS Photos using a natural-language prompt.",
    )
    ap.add_argument("prompt", nargs="?", help="Natural-language selection criteria.")
    ap.add_argument("--prompt-file", help="Read prompt from a file instead.")
    ap.add_argument("--format", choices=FORMAT_CHOICES, default="mp4",
                    help="Output format (default: mp4)")
    ap.add_argument("--max", dest="max_photos", type=int, default=40,
                    help="Max photos in the slideshow (default: 40)")
    ap.add_argument("--no-vision", action="store_true",
                    help="Disable vision-based curation (cheaper, faster, text-only).")
    ap.add_argument("--output-dir", default=str(Path.home() / "Pictures" / "PhotoSlideshows"),
                    help="Where to save output (default: ~/Pictures/PhotoSlideshows)")
    args = ap.parse_args(argv)

    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    else:
        prompt = args.prompt or ""
    if not prompt:
        ap.error("Provide a prompt (positional) or --prompt-file.")

    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    params = {
        "prompt": prompt,
        "format": args.format,
        "max_photos": args.max_photos,
        "visual_curation": not args.no_vision,
    }

    def log(token: str) -> None:
        print(f"  {token}", file=sys.stderr, flush=True)

    try:
        out = run_pipeline(params, output_dir, on_progress=log)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Stdout = just the path, for easy consumption by Shortcuts
    print(str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
