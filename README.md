# Photo Slideshow

A macOS menu bar app that reads your Photos library, uses Claude AI to intelligently
curate photos, and produces a slideshow in the format of your choice.

```
You: "sunset shots from our Morocco trip, me and my wife"
  ↓  text/keyword search of Photos library
  ↓  Claude Opus (vision) — examines every candidate photo
        • composition  • exposure  • sharpness  • people shots
        • eliminates burst/duplicate sequences (scene_group)
        • arranges for narrative flow
  ↓
MP4 | PPTX/Keynote | HTML | Photos Album
```

## Install

```bash
cd photo_slideshow
./install.sh
```

Add your Anthropic API key to `~/.zshrc` (or `~/.zprofile` for login shells):
```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

MP4 output requires ffmpeg:
```bash
brew install ffmpeg
```

## Permissions (one-time)

macOS will prompt the first time each permission is needed:

1. **Full Disk Access** — required so osxphotos can read the Photos library.
   System Settings → Privacy & Security → Full Disk Access → add Terminal (or Warp).

2. **Automation → Photos** — only needed for the "Photos Album" output format.
   Granted automatically the first time an album is created.

3. **Notifications** — optional; only fires if bundled as a `.app` via py2app.

## Run

### Menu bar app

```bash
source .venv/bin/activate
python main.py
```

A 📸 icon appears in your menu bar. Click it → **New Slideshow…** to open the dialog.

#### Dialog window fields

**Prompt**
Describe the slideshow in natural language — e.g. *"our Morocco trip, sunset shots,
me and my wife"*.

**Options**
- *Output format* — MP4, Keynote/PPTX, HTML, Photos Album
- *Max photos* — target slideshow length (default 40)
- *Visual curation* — checked by default; uses Claude vision to examine thumbnails.
  Uncheck for faster/cheaper text-only selection.

**Scan Limit**
Controls how many photos are pulled from your library as candidates:
- *Limit by date range* — click the 📅 buttons to choose From/To dates.
  Scans every photo in the window (no count cap). Uncheck either end for an open range.
- *Limit by photo count* — scan up to N photos from the library (default 400).

**Random** (applies to both scan modes)
When checked, matched photos are shuffled before curation so you get a different
selection each run. Set the "pick N random" count independently of the scan limit.

**Curation Prompt** (editable — sent to Claude with your photos)
Click **Build Prompt** to generate a full natural-language instruction from your
description. This tells Claude exactly what to look for and why. Read and edit it
before generating — useful for adding specific people, adjusting tone, or adding
constraints. When populated, the pipeline skips the Claude filter-spec step (faster).

**Generate** (`Cmd+Return`) — runs the full pipeline.

### CLI (for Shortcuts, Raycast, Alfred)

```bash
source .venv/bin/activate
python cli.py "favorites from Colorado trips, landscape orientation" \
    --format html --max 30
```

Flags:
- `--format {mp4,pptx,html,album}` (default `mp4`)
- `--max N` — target slideshow length (default 40)
- `--no-vision` — skip vision curation (cheaper/faster, text-only)
- `--output-dir PATH` — default `~/Pictures/PhotoSlideshows`
- `--prompt-file PATH` — read prompt from a file

The CLI prints the output file path on stdout — works well with Shortcuts'
"Run Shell Script" → "Open File" flow.

### macOS Shortcut

1. **Ask for Input** — "What kind of slideshow?"
2. **Run Shell Script**
   ```bash
   cd ~/path/to/photo_slideshow
   source .venv/bin/activate
   python cli.py "$1" --format mp4
   ```
3. **Open File** — using the stdout path.

Assign a global hotkey in System Settings → Keyboard → Keyboard Shortcuts → Services.

## How it works

### 1. Photo query
Your prompt words search the Photos library via `osxphotos.PhotosDB().query(...)`,
respecting the Scan Limit you chose.

**Automatic fallback** — if the query returns nothing (common without GPS data):
1. *Keyword OR album search* — separate keyword and album substring searches (OR logic)
2. *Text-search* — scores photos by how many prompt words appear in metadata
3. *Vision fallback* — when visual curation is on, returns a broad pool for Claude
   to identify matches visually

### 2. Scene group tagging
Before curation, every candidate photo gets a `scene_group` integer:
- Photos with the same `burst_uuid` (Mac Photos burst sequences) share a group
- Photos taken within 10 seconds at the same GPS location share a group
- Photos taken within 5 seconds with no GPS also share a group

Claude is explicitly told: **pick at most ONE photo per scene_group.**

### 3. Curation (Claude Opus with vision)
Thumbnails are downsized to 512 px and sent with metadata. Claude runs in two phases:

**Phase 1 — Examine every candidate:**
composition, exposure, sharpness, content relevance, people shots, near-duplicates

**Phase 2 — Select:**
1. Eliminate near-duplicates — keep only the best of each scene_group
2. Technical quality: sharp, well-exposed, well-composed
3. Include people/portrait shots
4. Narrative flow: establishing → details → people → atmosphere → strong close
5. Variety — no two consecutive photos from same scene or location

### 4. Generation
- **MP4**: ffmpeg; 4 s/image, 1920×1080 letterboxed, libx264/crf 20, 30 fps
- **PPTX**: python-pptx, 16:9, black background, opens in Keynote
- **HTML**: `_assets/` folder + HTML file with fade transitions, keyboard nav, fullscreen
- **Photos Album**: AppleScript (written to a temp file to avoid argument-list limits)
  creates a new album — play with File → Play Slideshow

## Cost per slideshow (rough)

Assuming 40-photo output from ~160 candidates with visual curation:
- With **Build Prompt** (no filter-spec call): ~$0.15–0.40
- Without Build Prompt (filter-spec included): ~$0.155–0.405
- With `--no-vision`: ~$0.01

## Troubleshooting

**"No photos matched those filters"**
- Three fallback strategies run automatically before giving up.
- Ensure photos are in a named album or have keywords in Photos.app —
  place-based search requires GPS metadata.
- Check your library: `osxphotos query --uuid-only | wc -l`
- Confirm Full Disk Access is granted.

**Burst shots still appearing**
- Keep Visual Curation checked — Claude needs thumbnails to apply scene_group rules.
- `--no-vision` uses text-only metadata and may not deduplicate as aggressively.

**"iCloud-only" photos missing from MP4/PPTX/HTML**
- Enable "Download Originals to this Mac" in Photos preferences, or use
  **Photos Album** format (works purely off UUIDs).

**AppleScript error creating album**
- Grant Automation permission: System Settings → Privacy & Security → Automation.

**ffmpeg crashes or "Invalid argument"**
- Usually a corrupt image file. Check stderr for the filename.

**Model / API errors**
- Verify `ANTHROPIC_API_KEY` is set in the shell running the app.
- If launched from Finder, set the key in `~/.zprofile` (login shells).

**DecompressionBombWarning (Pillow)**
- Not an error — the image size limit is raised automatically to 300 MP for
  high-res iPhone photos and panoramas.

## Testing

All tests run without an API key or Photos library (external calls are mocked):

```bash
source .venv/bin/activate
python test_slideshow.py
```

41 tests across 9 classes:

| Class | Tests | What's covered |
|---|---|---|
| `TestDatePicker` | 7 | Widget state, defaults, ISO display, Jan-1 default |
| `TestParseJsonLoose` | 5 | Trailing prose, code fences, multi-object responses |
| `TestPipelineScanModes` | 7 | Date range, count, random, open range, defaults |
| `TestSceneGroups` | 6 | Burst UUID, time-proximity (5 s/10 s), geo, solo |
| `TestCurationPromptWorkflow` | 3 | Skips filter spec, passes curation_prompt to Claude |
| `TestAlbumGeneration` | 2 | Temp file used, AppleScript contains all UUIDs |
| `TestPillowLimit` | 1 | MAX_IMAGE_PIXELS raised to 300 MP |
| `TestRandomSampling` | 2 | Trims to pick count, photos are subset of matched |
| `TestFallbackQuery` | 3 | OR logic, no AND, returns empty when nothing matches |

## File layout

```
photo_slideshow/
├── main.py                  # rumps menu bar app (📸 icon)
├── dialog.py                # subprocess launcher for the dialog window
├── _dialog_subprocess.py    # Tkinter window UI (runs in its own process)
├── cli.py                   # command-line entry point
├── pipeline.py              # orchestrator: query → scene-group → curate → generate
├── claude_client.py         # Anthropic API wrappers (filter spec + curation)
├── generators.py            # mp4 / pptx / html / album output
├── test_slideshow.py        # 41-test unit + integration suite
├── requirements.txt
├── install.sh
└── README.md
```

> **Why a subprocess for the dialog?** Tkinter and rumps both need the macOS
> main thread. Running the dialog in a child process gives it an isolated event
> loop with no conflict.

## Tweak points

- **Slide duration / transitions** — `generators.py` → `_generate_mp4` (`DURATION`, `FPS`)
  and `_render_html` (`INTERVAL` constant in JS).
- **Aspect ratio** — change `TARGET` in `_generate_mp4` to `(1080, 1920)` for
  vertical or `(1080, 1080)` for square.
- **Curation system prompt** — `claude_client.py` → `_CURATE_SYSTEM`. Adjust
  emphasis on drama, chronology, location variety, etc.
- **Thumbnail size** — `pipeline.py` → `_build_candidate_records` →
  `img.thumbnail((512, 512))`. Smaller = cheaper; larger = Claude sees more detail.
- **Scan limit default** — `pipeline.py` → the `400` cap in the `else` branch of
  the scan-mode block.

## Bundling as a .app (optional)

```bash
pip install py2app
# write a minimal setup.py, then:
python setup.py py2app
```

Not included to keep the scope lean. `python main.py` works fine for everyday use.
