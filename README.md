# Photo Slideshow

A macOS menu bar app that reads your Photos library, uses Claude to turn a
natural-language prompt into a filter + curation decision, and produces a
slideshow in the format of your choice.

```
You: "sunset shots from our Morocco trip, me and my wife, no selfies"
  ↓  (Claude Sonnet → filter spec)
osxphotos query against Photos.app SQLite catalog
  ↓  (Claude Opus w/ vision → selection + ordering)
MP4 | PPTX/Keynote | HTML | Photos album
```

## Install

```bash
cd photo_slideshow
./install.sh
```

Set your API key in `~/.zshrc`:
```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Optional but recommended:
```bash
brew install ffmpeg   # required for MP4 output
```

## Permissions (one-time)

macOS will prompt for these the first time each is needed:

1. **Full Disk Access** for your terminal (or for `python` if launched another way).
   *Required so osxphotos can read `~/Pictures/Photos Library.photoslibrary`.*
   System Settings → Privacy & Security → Full Disk Access → add Terminal.

2. **Automation → Photos**, only if you use the "Photos Album" output format.
   *Granted via a permission prompt the first time an album is created.*

3. **Notifications** (optional). Only works if you bundle as a .app via py2app.

## Run

### Menu bar app
```bash
source .venv/bin/activate
python main.py
```
Click 📸 → **New Slideshow…** to open the dialog window.

#### Dialog window fields
- **Prompt** — describe your slideshow in natural language (scrollable text box).
- **Output format** — dropdown: MP4, Keynote/PPTX, HTML, Photos Album.
- **Max photos** — spinbox, default 40.
- **Visual curation** — checkbox; uncheck for faster/cheaper text-only curation.
- **Filter JSON** (editable) — click **Preview Filter** to call Claude and see the
  exact filter spec that will be sent to your Photos library. Edit it freely
  before clicking **Generate** — useful for debugging or fine-tuning results.
- **Generate** (`Cmd+Return`) — runs the pipeline with the current settings.
  If the Filter JSON box is populated, that spec is used directly (skipping
  the first Claude call and saving ~$0.005).

### CLI (for Shortcuts, Raycast, Alfred)
```bash
python cli.py "favorites from Colorado trips, landscape orientation" \
    --format html --max 30
```

Flags:
- `--format {mp4,pptx,html,album}` (default `mp4`)
- `--max N` — target slideshow length (default 40)
- `--no-vision` — skip Opus vision curation (cheaper/faster, text-only)
- `--output-dir PATH` — default `~/Pictures/PhotoSlideshows`
- `--prompt-file PATH` — read prompt from a file (useful for multi-line)

The CLI prints the resulting file path on stdout, which plays nicely with
Shortcuts' "Run Shell Script" action.

### macOS Shortcut wrapping

Create a Shortcut with:
1. **Ask for Input** (text) — "What kind of slideshow?"
2. **Run Shell Script**
   ```bash
   cd ~/path/to/photo_slideshow
   source .venv/bin/activate
   python cli.py "$1" --format mp4
   ```
   Pass input: as arguments.
3. **Open File** — using the stdout path.

Assign a global shortcut key in System Settings → Keyboard → Keyboard Shortcuts → Services.

## How it works

1. **Filter spec** (`claude-sonnet-4-6`, text-only). Your prompt is converted
   into a structured JSON filter: date range, albums, keywords, persons,
   places, orientation, favorites, limits. You can preview and edit this spec
   in the dialog before generating.
2. **Photos query** via `osxphotos.PhotosDB().query(...)`. Returns up to
   `target × 4` candidates (capped at 400).
   
   **Automatic fallback** — if the strict filter returns nothing (common when
   photos lack GPS location data), the pipeline retries with two progressively
   looser strategies:
   - *Album/keyword fallback*: replaces the `places` filter with album and
     keyword substring searches using the significant words from your prompt.
     Finds photos in an album called "Morocco" even without GPS tagging.
   - *Text-search fallback*: loads all photos in the original date range and
     scores each one by how many prompt words appear in its place name,
     keywords, album names, or filename. Returns the best matches.
3. **Curation** (`claude-opus-4-7` with vision by default). Thumbnails are
   downsized to 512px max-edge and sent alongside metadata. Claude returns
   an ordered UUID list plus rationale.
4. **Generation**:
   - **MP4**: ffmpeg concat demuxer; 4s per image, 1920×1080 letterboxed,
     libx264/crf 20, 30fps.
   - **PPTX**: `python-pptx`, 16:9, black background, opens cleanly in Keynote.
   - **HTML**: self-contained folder (`..._assets/`) + single HTML file with
     fade transitions, keyboard nav, fullscreen.
   - **Photos Album**: AppleScript creates a new album in Photos.app — use
     native slideshow (File → Play Slideshow).

## Cost per slideshow (rough)

Assuming 40-photo output from ~160 candidates:
- Stage 1 (filter spec): ~$0.005
- Stage 2 with vision: ~$0.15–0.40 depending on thumbnail count.
- Stage 2 without vision (`--no-vision`): ~$0.01.

## Troubleshooting

**"No photos matched those filters"**
- The app now retries automatically with album/keyword and text-search
  fallbacks before giving up — so this error only appears if all three
  strategies found nothing.
- Use **Preview Filter** in the dialog to see the generated JSON and edit it
  (e.g. remove a `places` filter and add an `albums` filter manually).
- Ensure your trip photos are in a named album or have keywords applied in
  Photos.app — place-based filtering requires GPS location metadata.
- Check osxphotos directly: `osxphotos query --uuid-only | wc -l`.
- Confirm Full Disk Access is granted.

**"iCloud-only" photos missing from MP4/PPTX/HTML**
- Those formats need the original file on disk. Either enable "Download
  Originals to this Mac" in Photos preferences, or use the **Photos Album**
  format which works purely off UUIDs.

**AppleScript error creating album**
- Grant Terminal (or Python) Automation permission for Photos:
  System Settings → Privacy & Security → Automation.

**ffmpeg "Invalid argument" or crashes**
- Usually a weird image file. Check stderr output; the code already skips
  unreadable images but will abort if ffmpeg itself rejects a frame.

**Model / API errors**
- Check `ANTHROPIC_API_KEY` is set in the shell actually running the app.
- Menu bar apps inherit the environment of the shell they're launched from;
  if you launch from Finder, set the key in `~/.zprofile` (login shells).

## Testing

Run the test suite (no API key or Photos library required — all external calls are mocked):

```bash
source .venv/bin/activate
python test_slideshow.py
```

19 tests across three classes:

- **`TestDatePicker`** (7 tests) — `_DatePicker` widget: default and custom dates, ISO
  display string, enable/disable state, Jan-1 default for the From picker.
- **`TestParseJsonLoose`** (5 tests) — `_parse_json_loose`: clean JSON, trailing prose
  after the object, multiple JSON objects in one response, code-fenced JSON,
  realistic filter spec shape.
- **`TestPipelineScanModes`** (7 tests) — `run_pipeline` scan limit logic: date range
  patches the spec and sets limit to 50,000; count mode uses `scan_limit`; minimum
  clamp to `max_photos`; legacy default cap of 400; `random_sample` flag; open-ended
  date range (both ends `None`).

## File layout

```
photo_slideshow/
├── main.py                  # rumps menu bar app
├── dialog.py                # subprocess launcher for the dialog window
├── _dialog_subprocess.py    # Tkinter window UI (runs in its own process)
├── cli.py                   # command-line entry point
├── pipeline.py              # orchestrator: filter → query → curate → generate
├── claude_client.py         # Anthropic API wrappers
├── generators.py            # mp4 / pptx / html / album
├── test_slideshow.py        # unit + integration test suite
├── requirements.txt
├── install.sh
└── README.md
```

> **Why a subprocess for the dialog?** Tkinter and rumps both require control
> of the macOS main thread. Running the dialog in a child process gives it an
> isolated event loop with no conflict.

## Tweak points

- **Slide duration / transitions** — `generators.py` → `_generate_mp4` (`DURATION`, `FPS`)
  and `_render_html` (`INTERVAL` const in JS).
- **Target aspect** — change `TARGET` in `_generate_mp4` to 1080×1920 for vertical,
  or 1080×1080 for square.
- **Curation prompt** — `claude_client.py` → `_CURATE_SYSTEM`. Bias toward drama,
  variety, chronology, etc.
- **Max candidates** — `pipeline.py` → the `400` cap in `run_pipeline`.
- **Thumbnail size for vision** — `pipeline.py` → `_build_candidate_records`
  (`img.thumbnail((512, 512))`). Smaller = cheaper but Claude sees less.

## Bundling as a proper .app (optional)

For notifications and a proper macOS app icon:

```bash
pip install py2app
python setup.py py2app     # (you'd write a small setup.py)
```

Not included here to keep the scope lean. The plain `python main.py`
invocation works fine for everyday use.
