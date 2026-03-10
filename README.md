# zoomdownloader

Scrape, browse, and upload guitar effect patches for the **Zoom G3n / G3Xn / G5n**
from the [Tonelib community forum](https://tonelib.net/forums/forums/zoom-g3n-g3xn-g5n.15/).

## Features

- Crawls every page of the forum and indexes all threads as JSON files
- Extracts structured metadata: device, firmware, name on device, optimized for, patch comments, forum URL, and download links
- Saves session cookie so you only need to log in once
- Terminal UI browser with arrow-key navigation, live search, detail view, and one-key patch download
- Uploads patches directly to the pedal via USB/MIDI SysEx (`upload` command)

## Installation

### pip (recommended)

```bash
pip install zoomdownloader
```

The `zoomdownloader` command is immediately available after installation.

### Debian / Ubuntu (.deb)

Download the latest `.deb` from the [Releases](../../releases) page and install:

```bash
sudo dpkg -i zoomdownloader_*.deb
```

Dependencies (`python3`, `python3-venv`) are installed automatically.  
Python packages are fetched into a per-user virtual environment on first run.

### From source

```bash
git clone <repo>
cd zoomdownloader
pip install -e .
```

## Requirements

- Python 3.10+
- A free account on [tonelib.net](https://tonelib.net/forums/)
- For `upload`: the Zoom pedal connected via USB and the ALSA MIDI subsystem (`libasound2`)

## Usage

### Scrape the forum

```bash
zoomdownloader scrape
```

You will be prompted for your Tonelib username and password on the first run.  
Credentials and the session cookie are stored in `~/.zoomdownloader/config/` and reused on subsequent runs.  
Scraped patches are written to `~/.zoomdownloader/index/<thread-id>.json` — already-indexed threads are skipped automatically.

```bash
zoomdownloader scrape --force    # re-scrape threads already in the index
zoomdownloader scrape --debug    # verbose logging + save raw HTML to debug/
```

### Browse patches (TUI)

```bash
zoomdownloader browse
zoomdownloader          # browse is the default when no sub-command is given
```

| Key | Action |
|-----|--------|
| `↑` / `↓` | Navigate the patch list |
| `/` | Focus the search field |
| `Esc` | Clear search / close detail view |
| `Enter` | Open detail view |
| `d` | Download the selected patch file |
| `r` | Reload index from disk |
| `q` | Quit |

Downloaded files are saved to `~/.zoomdownloader/downloads/`.

```bash
zoomdownloader browse --debug    # enable debug logging to debug/browse.log
```

### Upload a patch to the pedal

```bash
zoomdownloader upload PATH/TO/PATCH.zg3xn SLOT
```

- `SLOT` is the target memory slot number (0–199).
- Supported file extensions: `.zg3n`, `.zg3xn`, `.zg5n`.
- The pedal must be connected via USB. The tool communicates over raw ALSA MIDI SysEx.

Example:

```bash
zoomdownloader upload ~/Downloads/crunch.zg3xn 5
```

## Data locations

| Path | Contents |
|------|----------|
| `~/.zoomdownloader/index/` | Scraped patch metadata (JSON, one file per forum thread) |
| `~/.zoomdownloader/downloads/` | Downloaded `.zg*` patch files |
| `~/.zoomdownloader/config/cookies.json` | Session cookie |
| `~/.zoomdownloader/config/credentials.json` | Saved login credentials |

## Project layout

```
zoomdownloader/
├── main.py          # CLI entry point (scrape / browse / upload)
├── scraper.py       # Forum crawler & patch parser
├── viewer.py        # Textual TUI browser
├── zoom_midi.py     # ALSA MIDI / SysEx communication with the pedal
├── requirements.txt
├── pyproject.toml   # pip package definition
├── bin/
│   └── zoomdownloader   # launcher script (used by the .deb package)
└── debian/
    └── control          # .deb package metadata template
```

