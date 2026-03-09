# zoomdownloader

Scrape and browse guitar effect patches for the **Zoom G3n / G3Xn / G5n** from the [Tonelib community forum](https://tonelib.net/forums/forums/zoom-g3n-g3xn-g5n.15/).

## Features

- Crawls every page of the forum and indexes all threads as JSON files
- Extracts structured metadata: device, firmware, name on device, optimized for, patch comments, forum URL, and download links
- Saves session cookie so you only need to log in once
- Terminal UI browser with arrow-key navigation, live search, detail view, and one-key patch download

## Requirements

- Python 3.10+
- A free account on [tonelib.net](https://tonelib.net/forums/)

## Setup

```bash
git clone <repo>
cd zoomdownloader
python3 -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Usage

### Scrape the forum

```bash
.venv/bin/python main.py scrape
```

You will be prompted for your Tonelib username and password on the first run.  
The session is saved to `cookies.json` and reused on subsequent runs.  
Scraped patches are written to `index/<thread-id>.json` – already-indexed threads are skipped automatically.

To force a full re-scrape:

```bash
.venv/bin/python main.py scrape --force
```

### Browse patches (TUI)

```bash
.venv/bin/python main.py browse
# or just
.venv/bin/python main.py
```

| Key | Action |
|-----|--------|
| `↑` / `↓` | Navigate the list |
| `/` | Focus the search field |
| `Esc` | Clear search / go back |
| `Enter` | Open detail view |
| `d` | Download patch file |
| `r` | Reload index from disk |
| `q` | Quit |

Downloaded files are saved to `downloads/`.

## Project layout

```
zoomdownloader/
├── main.py          # CLI entry point  (scrape / browse)
├── scraper.py       # Forum crawler & patch parser
├── viewer.py        # Textual TUI browser
├── requirements.txt
├── index/           # Scraped patch metadata (JSON)
└── downloads/       # Downloaded .zg* patch files
```

