# index

This directory contains scraped patch metadata as JSON files.

Each file is named `<thread-id>.json` and holds the following fields:

| Field | Description |
|---|---|
| `title` | Thread title from the forum |
| `device` | Target device (G3n / G3Xn / G5n) |
| `firmware` | Firmware version the patch was made for |
| `name_on_device` | Short name as it appears on the device display |
| `optimized_for` | Guitar style, amp, or genre the patch targets |
| `patch_comments` | Full text of the forum post / patch notes |
| `forum_url` | Direct link to the forum thread |
| `download_urls` | List of `.zg*` patch file download links |

Populate this directory by running:

```bash
.venv/bin/python main.py scrape
```
