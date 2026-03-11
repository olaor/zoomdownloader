# zoomdownloader

Scrape, browse, and upload guitar effect patches for the **Zoom G3n / G3Xn / G5n**
from the [Tonelib community forum](https://tonelib.net/forums/forums/zoom-g3n-g3xn-g5n.15/).

> [!WARNING]
> **No warranty. Use at your own risk.**
>
> This software is provided as-is with **no warranties of any kind**. There are known,
> unresolved bugs that can cause the pedal to crash so severely that it requires a
> **factory reset**, which will **erase all installed patches**.
>
> **Back up your pedal before use.** If you have a carefully curated set of patches
> on your pedal, use [ToneLib-Zoom](https://tonelib.net/) to create a full backup
> before running this tool. A factory reset cannot be undone.

## Features

- Crawls every page of the forum and indexes all threads as JSON files
- Extracts structured metadata: device, firmware, name on device, optimized for, patch comments, forum URL, and download links
- Saves session cookie so you only need to log in once
- Terminal UI browser with arrow-key navigation, live search, detail view, and one-key patch download
- **Preferences** (`p`): set your pedal model (G3n, G3Xn, G3Xn+G3n, or G5n) and firmware version, then enable filtering so only patches made for your hardware are shown
- **Favourites** tab: curate a personal set of patches, organize them into named groups, reorder freely, and upload the whole collection to the pedal in one operation — each favourite entry shows the target pedal model and firmware at a glance
- Uploads patches directly to the pedal via USB/MIDI SysEx (`upload` command)
- **G5n patch compatibility**: G5n patches (8–9 effect slots) are automatically adapted for the G3n/G3Xn firmware (7-slot limit) on upload — no skipping, no crashes
- **Group padding**: when a group has fewer patches than the 3-slot bank size, the leftover slot is automatically cleared on the pedal so no stale patch is left behind

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
| `u` | Upload selected patch to the pedal |
| `t` | Test selected patch on the pedal (temporary, non-destructive) |
| `f` | Add/remove patch from Favourites |
| `r` | Reload index from disk |
| `p` | Open Preferences (pedal model, firmware, filter toggle) |
| `q` | Quit |

The patch list columns are: **Title**, **Name on device**, **Device**, **Firmware**, **Optimized for**, **DL** (download count), and **★** (favourited).

### Preferences

Press `p` to open the Preferences dialog:

- **Pedal model** — choose from G3n, G3Xn, G3Xn + G3n (both), or G5n
- **Firmware version** — enter your firmware (e.g. `2.20`); leave blank to match any
- **Filter toggle** — when checked, the patch list shows only patches whose `device` and `firmware` fields match your selection

When `G3Xn + G3n` is selected, patches tagged for either model are shown together.

Preferences are saved to `~/.zoomdownloader/prefs.json`.

### Favourites

The **Favourites** tab (press `2` or click the tab) holds your curated patch collection.
Patches can be organised into named groups.

| Key | Action |
|-----|--------|
| `f` | Add selected patch to Favourites (from All Patches tab) |
| `f` | Remove patch from Favourites (from Favourites tab) |
| `Enter` | Open detail view for selected favourite |
| `n` | New group |
| `r` | Rename selected group |
| `m` | Move patch into a group / make standalone |
| `c` | Copy patch to a group |
| `u` | Upload all Favourites to the pedal (bank-aligned) |
| `Shift+↑` / `Shift+↓` | Reorder patches (move into/out of adjacent groups) |

Each entry in the Favourites list shows the patch title alongside its **pedal model** and **firmware** (e.g. `• JOLA LEAD  G3Xn  fw 2.10`), so you can see at a glance which hardware a patch targets.

Favourites are saved to `~/.zoomdownloader/favourites.json`.

Downloaded files are saved to `~/.zoomdownloader/downloads/`.

```bash
zoomdownloader browse --debug    # enable debug logging to debug/browse.log
```

### Upload a patch to the pedal

From the TUI browser, select a patch and press `u`. You will be prompted for a
target slot number (1–200). **Leave the field blank and press Enter to upload to
the currently active slot on the pedal** — no need to remember which number you
are on.

You can also upload directly from the command line:

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
| `~/.zoomdownloader/favourites.json` | Favourites collection (patches + groups) |
| `~/.zoomdownloader/prefs.json` | Preferences (pedal model, firmware, filter toggle) |

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

