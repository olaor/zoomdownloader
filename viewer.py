"""
Textual TUI for browsing and downloading Zoom G3n/G3Xn/G5n patches.
Run via main.py or directly: python viewer.py
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    OptionList,
    Static,
)

BASE_DIR     = Path(__file__).parent
APP_DIR      = Path.home() / ".zoomdownloader"
INDEX_DIR    = APP_DIR / "index"
DOWNLOAD_DIR = APP_DIR / "downloads"
DEBUG_DIR    = BASE_DIR / "debug"

log = logging.getLogger("zoomdownloader.browse")


# ── data loading ─────────────────────────────────────────────────────────────

def load_patches() -> list[dict]:
    if not INDEX_DIR.exists():
        return []
    patches = []
    for p in sorted(INDEX_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text())
            data["_file"] = p.name
            data["_id"] = p.stem        # e.g. "18732"
            patches.append(data)
        except Exception:
            pass
    return patches


def _field(patch: dict, key: str, default: str = "—") -> str:
    v = patch.get(key, "").strip()
    return v if v else default


# ── Detail screen ─────────────────────────────────────────────────────────────

class DetailScreen(Screen):
    """Full-screen detail view for a single patch."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape,q", "dismiss", "Back"),
        Binding("d", "download", "Download patch"),
        Binding("u", "upload_to_pedal", "Upload to pedal"),
        Binding("t", "test_patch", "Test patch"),
    ]

    DEFAULT_CSS = """
    DetailScreen {
        align: center middle;
    }
    #detail-outer {
        width: 100%;
        height: 100%;
        background: $surface;
    }
    #detail-scroll {
        height: 1fr;
        padding: 1 2;
    }
    #detail-content {
        width: 100%;
    }
    #detail-urls {
        margin-top: 1;
        padding: 1;
        border: solid $primary-darken-2;
    }
    """

    def __init__(self, patch: dict) -> None:
        super().__init__()
        self.patch = patch

    def compose(self) -> ComposeResult:
        p = self.patch
        yield Header(show_clock=False)

        with Vertical(id="detail-outer"):
            with ScrollableContainer(id="detail-scroll"):
                lines: list[str] = [
                    f"[bold cyan]Title:[/]         {_field(p, 'title')}",
                    f"[bold cyan]Device:[/]        {_field(p, 'device')}",
                    f"[bold cyan]Firmware:[/]      {_field(p, 'firmware')}",
                    f"[bold cyan]Name on device:[/] {_field(p, 'name_on_device')}",
                    f"[bold cyan]Optimized for:[/] {_field(p, 'optimized_for')}",
                    "",
                    f"[bold cyan]Forum URL:[/]",
                    f"  {_field(p, 'forum_url')}",
                ]

                urls = p.get("download_urls", [])
                lines += ["", f"[bold cyan]Download URLs ({len(urls)}):[/]"]
                if urls:
                    for i, u in enumerate(urls, 1):
                        lines.append(f"  [{i}] {u}")
                else:
                    lines.append("  (none found)")

                lines += ["", f"[bold cyan]Comments / patch notes:[/]", ""]
                comments = p.get("patch_comments", "").strip()
                lines.append(comments if comments else "(empty)")

                yield Static("\n".join(lines), id="detail-content", markup=True)

        yield Footer()

    def action_dismiss(self) -> None:  # type: ignore[override]
        self.app.pop_screen()

    def action_download(self) -> None:
        urls = self.patch.get("download_urls", [])
        if not urls:
            self.notify("No download URL available for this patch.", severity="warning")
            return
        self.app.trigger_download(urls[0], self.patch)

    def action_upload_to_pedal(self) -> None:
        urls = self.patch.get("download_urls", [])
        if not urls:
            self.notify("No download URL available for this patch.", severity="warning")
            return
        title = _field(self.patch, "title", "patch")
        log.debug("DetailScreen.action_upload_to_pedal: title=%r url=%s", title, urls[0])
        self.app.start_upload_flow(urls[0], self.patch)

    def action_test_patch(self) -> None:
        urls = self.patch.get("download_urls", [])
        if not urls:
            self.notify("No download URL available for this patch.", severity="warning")
            return
        title = _field(self.patch, "title", "patch")
        log.debug("DetailScreen.action_test_patch: title=%r url=%s", title, urls[0])
        self.app.start_test_flow(urls[0], self.patch)


# ── Download-choice modal (for multiple URLs) ─────────────────────────────────

class DownloadChoiceModal(ModalScreen[int | None]):
    """Ask user to pick one URL when a patch has multiple download links."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "dismiss_none", "Cancel"),
    ]

    DEFAULT_CSS = """
    DownloadChoiceModal {
        align: center middle;
    }
    #modal-box {
        width: 70;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    #modal-box Label {
        margin-bottom: 1;
    }
    .choice-btn {
        width: 100%;
        margin-bottom: 0;
    }
    """

    def __init__(self, urls: list[str]) -> None:
        super().__init__()
        self.urls = urls

    def compose(self) -> ComposeResult:
        with Container(id="modal-box"):
            yield Label("[bold]Multiple download links found – choose one:[/bold]", markup=True)
            for i, url in enumerate(self.urls):
                label = url.split("/")[-1] or url
                yield Button(f"{i + 1}. {label}", id=f"btn-{i}", classes="choice-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        idx = int(event.button.id.split("-")[1])
        self.dismiss(idx)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class SlotInputModal(ModalScreen[int | None]):
    """Ask user for a pedal slot number to upload a patch to."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "dismiss_none", "Cancel"),
    ]

    DEFAULT_CSS = """
    SlotInputModal {
        align: center middle;
    }
    #slot-box {
        width: 50;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    #slot-box Label {
        margin-bottom: 1;
    }
    #slot-input {
        margin-bottom: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Container(id="slot-box"):
            yield Label("[bold]Upload to pedal slot:[/bold]", markup=True)
            yield Input(placeholder="Slot number (1–200)", id="slot-input", type="integer")
            yield Button("Upload", id="slot-ok", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        val = self.query_one("#slot-input", Input).value.strip()
        if val.isdigit():
            slot = int(val)
            if 1 <= slot <= 200:
                self.dismiss(slot)
                return
        self.notify("Enter a slot number between 1 and 200.", severity="warning")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.on_button_pressed(Button.Pressed(self.query_one("#slot-ok", Button)))

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class TestPatchConfirmModal(ModalScreen[bool]):
    """Ask user whether to keep a temporarily-loaded test patch in its slot."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("y", "confirm_keep", "Keep", show=False),
        Binding("n", "discard", "Discard", show=False),
        Binding("escape", "discard", "Discard"),
        Binding("q", "discard", "Discard", show=False),
    ]

    DEFAULT_CSS = """
    TestPatchConfirmModal {
        align: center middle;
    }
    #test-box {
        width: 62;
        height: auto;
        border: thick $success;
        background: $surface;
        padding: 1 2;
    }
    #test-box Label {
        margin-bottom: 1;
    }
    #test-buttons {
        height: auto;
    }
    #test-keep {
        width: 1fr;
    }
    #test-discard {
        width: 1fr;
    }
    """

    def __init__(self, patch_name: str) -> None:
        super().__init__()
        self.patch_name = patch_name

    def compose(self) -> ComposeResult:
        with Container(id="test-box"):
            yield Label(
                f'[bold green]"{self.patch_name}"[/bold green] is now active on your pedal.\n'
                "Keep it?",
                markup=True,
            )
            with Horizontal(id="test-buttons"):
                yield Button("[Y] Keep", id="test-keep", variant="success")
                yield Button("[N/Esc/Q] Discard", id="test-discard", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "test-keep":
            self.action_confirm_keep()
        else:
            self.action_discard()

    def action_confirm_keep(self) -> None:
        self.dismiss(True)

    def action_discard(self) -> None:
        self.dismiss(False)

    def on_mount(self) -> None:
        self.query_one("#test-discard", Button).focus()


class PatchFileChoiceModal(ModalScreen["list[Path] | None"]):
    """Pick one patch file or upload all."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "dismiss_none", "Cancel"),
    ]

    DEFAULT_CSS = """
    PatchFileChoiceModal {
        align: center middle;
    }
    #pf-box {
        width: 70;
        height: auto;
        max-height: 80%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    #pf-box Label {
        margin-bottom: 1;
    }
    #pf-list {
        height: auto;
        max-height: 50%;
        margin-bottom: 1;
    }
    #pf-upload-one {
        width: 1fr;
    }
    #pf-upload-all {
        width: 1fr;
    }
    """

    def __init__(self, patch_files: list[Path]) -> None:
        super().__init__()
        self.patch_files = patch_files

    def compose(self) -> ComposeResult:
        with Container(id="pf-box"):
            yield Label(
                f"[bold]{len(self.patch_files)} patch files \u2013 pick one or upload all:[/bold]",
                markup=True,
            )
            yield OptionList(*[pf.name for pf in self.patch_files], id="pf-list")
            with Horizontal(id="pf-buttons"):
                yield Button("Upload selected", id="pf-upload-one", variant="primary")
                yield Button("Upload all", id="pf-upload-all", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "pf-upload-all":
            self.dismiss(self.patch_files)
        else:
            idx = self.query_one("#pf-list", OptionList).highlighted
            if idx is not None:
                self.dismiss([self.patch_files[idx]])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss([self.patch_files[event.option_index]])

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


# ── Main browser screen ───────────────────────────────────────────────────────

class BrowserScreen(Screen):
    """Main file-list screen with search."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit_app", "Quit"),
        Binding("/", "focus_search", "Search", show=True),
        Binding("escape", "clear_search", "Clear search", show=False),
        Binding("enter", "view_detail", "Details", show=True),
        Binding("d", "download_selected", "Download", show=True),
        Binding("u", "upload_selected", "Upload to pedal", show=True),
        Binding("t", "test_selected", "Test patch", show=True),
        Binding("r", "reload", "Reload index", show=True),
    ]

    DEFAULT_CSS = """
    BrowserScreen {
        layout: vertical;
    }
    #search-row {
        height: 3;
        padding: 0 1;
        background: $panel;
    }
    #search-input {
        width: 1fr;
    }
    #patch-table {
        height: 1fr;
    }
    #status-bar {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.all_patches: list[dict] = []
        self.filtered_patches: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="search-row"):
            yield Input(
                placeholder="  Type to filter  (press / to focus, Esc to clear)",
                id="search-input",
            )
        yield DataTable(id="patch-table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self._build_columns()
        self.reload_data()
        self.query_one("#patch-table", DataTable).focus()

    def _build_columns(self) -> None:
        t = self.query_one("#patch-table", DataTable)
        t.add_columns("Title", "Device", "Firmware", "Optimized for", "DL")

    def reload_data(self) -> None:
        self.all_patches = load_patches()
        self.filtered_patches = list(self.all_patches)
        self._refresh_table()

    def _refresh_table(self) -> None:
        t = self.query_one("#patch-table", DataTable)
        t.clear()
        for i, p in enumerate(self.filtered_patches):
            dl_count = len(p.get("download_urls", []))
            dl_cell = Text(str(dl_count), style="green" if dl_count else "red")
            t.add_row(
                _field(p, "title"),
                _field(p, "device"),
                _field(p, "firmware"),
                _field(p, "optimized_for"),
                dl_cell,
                key=str(i),
            )
        status = self.query_one("#status-bar", Static)
        total = len(self.all_patches)
        shown = len(self.filtered_patches)
        if total == 0:
            status.update(
                "[yellow]No patches indexed yet. Run:[/yellow] [bold]python main.py scrape[/bold]"
            )
        elif shown == total:
            status.update(f"{total} patches")
        else:
            status.update(f"{shown} of {total} patches match")

    # ── search ───────────────────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "search-input":
            return
        q = event.value.lower().strip()
        if not q:
            self.filtered_patches = list(self.all_patches)
        else:
            self.filtered_patches = [
                p
                for p in self.all_patches
                if q
                in " ".join(
                    [
                        p.get("title", ""),
                        p.get("device", ""),
                        p.get("firmware", ""),
                        p.get("optimized_for", ""),
                        p.get("name_on_device", ""),
                        p.get("patch_comments", ""),
                    ]
                ).lower()
            ]
        self._refresh_table()

    # ── key actions ──────────────────────────────────────────────────────────

    def action_focus_search(self) -> None:
        self.query_one("#search-input", Input).focus()

    def action_clear_search(self) -> None:
        inp = self.query_one("#search-input", Input)
        inp.value = ""
        self.query_one("#patch-table", DataTable).focus()

    def action_view_detail(self) -> None:
        patch = self._selected_patch()
        if patch is not None:
            self.app.push_screen(DetailScreen(patch))

    def action_download_selected(self) -> None:
        patch = self._selected_patch()
        if patch is None:
            return
        urls = patch.get("download_urls", [])
        if not urls:
            self.notify("No download URL for this patch.", severity="warning")
            return
        if len(urls) == 1:
            self.app.trigger_download(urls[0], patch)
        else:
            def _on_pick(idx: int | None) -> None:
                if idx is not None:
                    self.app.trigger_download(urls[idx], patch)

            self.app.push_screen(DownloadChoiceModal(urls), callback=_on_pick)

    def action_upload_selected(self) -> None:
        patch = self._selected_patch()
        if patch is None:
            log.debug("action_upload_selected: no patch selected")
            return
        urls = patch.get("download_urls", [])
        if not urls:
            self.notify("No download URL for this patch.", severity="warning")
            return
        title = _field(patch, "title", "patch")
        log.debug("action_upload_selected: title=%r urls=%d", title, len(urls))
        if len(urls) == 1:
            self.app.start_upload_flow(urls[0], patch)
        else:
            def _on_pick(idx: int | None) -> None:
                if idx is not None:
                    self.app.start_upload_flow(urls[idx], patch)

            self.app.push_screen(DownloadChoiceModal(urls), callback=_on_pick)

    def action_test_selected(self) -> None:
        patch = self._selected_patch()
        if patch is None:
            log.debug("action_test_selected: no patch selected")
            return
        urls = patch.get("download_urls", [])
        if not urls:
            self.notify("No download URL for this patch.", severity="warning")
            return
        title = _field(patch, "title", "patch")
        log.debug("action_test_selected: title=%r urls=%d", title, len(urls))
        if len(urls) == 1:
            self.app.start_test_flow(urls[0], patch)
        else:
            def _on_pick(idx: int | None) -> None:
                if idx is not None:
                    self.app.start_test_flow(urls[idx], patch)

            self.app.push_screen(DownloadChoiceModal(urls), callback=_on_pick)

    def action_reload(self) -> None:
        self.reload_data()
        self.notify("Index reloaded.")

    def action_quit_app(self) -> None:
        self.app.exit()

    # ── DataTable row selected via Enter ─────────────────────────────────────

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_view_detail()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _selected_patch(self) -> dict | None:
        t = self.query_one("#patch-table", DataTable)
        row_key = t.cursor_row
        log.debug("_selected_patch: cursor_row=%r type=%s  patches=%d",
                  row_key, type(row_key).__name__, len(self.filtered_patches))
        if row_key is None or not self.filtered_patches:
            return None
        # cursor_row is an integer index in Textual >= 0.45
        try:
            idx = int(str(row_key))
        except (TypeError, ValueError):
            log.warning("_selected_patch: cannot convert cursor_row %r to int", row_key)
            return None
        if 0 <= idx < len(self.filtered_patches):
            return self.filtered_patches[idx]
        log.warning("_selected_patch: idx=%d out of range (0..%d)", idx, len(self.filtered_patches) - 1)
        return None


# ── App ───────────────────────────────────────────────────────────────────────

class ZoomPatchBrowser(App):
    """Top-level Textual application."""

    TITLE = "Zoom Patch Browser  •  G3n / G3Xn / G5n"
    SUB_TITLE = "Tonelib forum index"

    CSS = """
    App {
        background: $background;
    }
    """

    def __init__(self, debug: bool = False) -> None:
        super().__init__()
        self._debug_mode = debug
        if debug:
            DEBUG_DIR.mkdir(exist_ok=True)
            log_path = DEBUG_DIR / "browse.log"
            handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s  %(message)s")
            )
            # Set up root "zoomdownloader" logger so midi module logs go here too
            root_log = logging.getLogger("zoomdownloader")
            root_log.setLevel(logging.DEBUG)
            root_log.addHandler(handler)
            log.debug("=== browse session started (debug=True) ===")
            log.debug("log file: %s", log_path)

    def on_mount(self) -> None:
        self.push_screen(BrowserScreen())
        if self._debug_mode:
            self.notify(
                "Debug logging → debug/browse.log",
                title="Debug ON",
                timeout=6,
            )

    # ── download orchestration ────────────────────────────────────────────────

    @staticmethod
    def _patch_dir(patch: dict) -> Path:
        """Return downloads/<id>/ for this patch, creating it if needed."""
        pid = patch.get("_id", "unknown")
        dest = DOWNLOAD_DIR / pid
        dest.mkdir(parents=True, exist_ok=True)
        return dest

    def trigger_download(self, url: str, patch: dict) -> None:
        """Called from any screen to start a background download."""
        self._do_download(url, patch)

    @work(thread=True)
    def _do_download(self, url: str, patch: dict) -> None:
        """Background worker: download one patch file."""
        from scraper import ForumScraper

        title = _field(patch, "title", "patch")
        dest_dir = self._patch_dir(patch)

        scraper = ForumScraper()
        scraper.load_cookies()

        try:
            saved = scraper.download_file(url, dest_dir, title=title)
            # Extract any archives (ZIP, RAR) the scraper didn't handle
            self._extract_archives(dest_dir)
            label = (
                f"Extracted → {dest_dir.name}/{saved.name}/"
                if saved.is_dir()
                else f"Saved → {dest_dir.name}/{saved.name}"
            )
            self.call_from_thread(
                self.notify, label, title="Download complete"
            )
        except Exception as exc:
            self.call_from_thread(
                self.notify,
                f"Failed: {exc}",
                title="Download error",
                severity="error",
            )

    # ── upload orchestration ──────────────────────────────────────────────────

    @staticmethod
    def _extract_archives(base: Path) -> None:
        """Extract any ZIP or RAR archives found directly in *base*."""
        import shutil
        import subprocess
        import zipfile

        for arc in list(base.iterdir()):
            if arc.is_dir():
                continue
            # Skip .zg* files — they are patch files (header + embedded ZIP)
            if arc.suffix.lower().startswith(".zg"):
                continue

            extracted = False

            # Try ZIP
            if zipfile.is_zipfile(arc):
                log.debug("_extract_archives: extracting ZIP %s", arc.name)
                with zipfile.ZipFile(arc) as zf:
                    zf.extractall(base)
                extracted = True

            # Try RAR (use unrar command-line tool)
            if not extracted and shutil.which("unrar"):
                try:
                    # Quick check: is this a RAR file? (magic bytes "Rar!")
                    with open(arc, "rb") as f:
                        magic = f.read(7)
                    if magic[:4] == b"Rar!":
                        log.debug("_extract_archives: extracting RAR %s", arc.name)
                        result = subprocess.run(
                            ["unrar", "x", "-o+", str(arc), str(base) + "/"],
                            capture_output=True, text=True, timeout=60,
                        )
                        if result.returncode == 0:
                            extracted = True
                        else:
                            log.warning("_extract_archives: unrar failed: %s", result.stderr)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    log.warning("_extract_archives: unrar error: %s", exc)

            if extracted:
                arc.unlink()
                log.debug("_extract_archives: removed archive %s", arc.name)

    @staticmethod
    def _find_patch_files(base: Path) -> list[Path]:
        """Recursively find .zg* and ToneLib.data patch files under *base*."""
        files: list[Path] = []
        for pattern in ("**/*.zg*", "**/ToneLib.data"):
            files.extend(base.glob(pattern))
        # De-duplicate, deterministic order
        seen: set[Path] = set()
        result: list[Path] = []
        for f in sorted(files):
            if f not in seen:
                seen.add(f)
                result.append(f)
        return result

    def start_upload_flow(self, url: str, patch: dict) -> None:
        """Download the patch, then ask for slot (and file if >1), then upload."""
        title = _field(patch, "title", "patch")
        log.info("start_upload_flow: url=%s title=%r", url, title)
        self._do_upload_download(url, patch)

    @work(thread=True)
    def _do_upload_download(self, url: str, patch: dict) -> None:
        """Background worker: download patch into downloads/<id>/ then hand off to UI."""
        from scraper import ForumScraper

        title = _field(patch, "title", "patch")
        dest_dir = self._patch_dir(patch)

        self.call_from_thread(
            self.notify, f"Downloading '{title}'…", title="Upload", timeout=8,
        )

        scraper = ForumScraper()
        cookies_ok = scraper.load_cookies()
        log.debug("upload-dl: cookies loaded = %s", cookies_ok)
        if not cookies_ok:
            log.warning("upload-dl: no session cookies — download may fail if auth is required")

        try:
            saved = scraper.download_file(url, dest_dir, title=title)
            log.info("upload-dl: saved → %s  (is_dir=%s)", saved, saved.is_dir())

            # Extract any archives (ZIP, RAR) before scanning for patch files
            self._extract_archives(dest_dir)

            # Collect all patch files under the download directory
            patch_files = self._find_patch_files(dest_dir)
            log.info("upload-dl: found %d patch file(s): %s",
                     len(patch_files), [f.name for f in patch_files])

            if not patch_files:
                contents = list(dest_dir.rglob("*"))
                log.error("upload-dl: no patch files in %s, contents: %s",
                          dest_dir, [c.name for c in contents])
                self.call_from_thread(
                    self.notify,
                    f"No patch files found in download. "
                    f"Contents: {[c.name for c in contents[:10]]}",
                    title="Upload error",
                    severity="error",
                    timeout=10,
                )
                return

            # Continue on the main thread to show modals
            self.call_from_thread(self._upload_pick_file_and_slot, patch_files, title)

        except Exception as exc:
            log.exception("upload-dl: FAILED")
            self.call_from_thread(
                self.notify,
                f"Download failed: {exc}",
                title="Upload error",
                severity="error",
                timeout=15,
            )

    def _upload_pick_file_and_slot(self, patch_files: list[Path], title: str) -> None:
        """For a single file go straight to slot prompt; for multiple show the picker."""
        if len(patch_files) == 1:
            self._upload_ask_slot(patch_files, title)
            return

        def _on_choice(chosen: "list[Path] | None") -> None:
            if chosen:
                self._upload_ask_slot(chosen, title)

        self.push_screen(PatchFileChoiceModal(patch_files), callback=_on_choice)

    def _upload_ask_slot(self, patch_files: list[Path], title: str) -> None:
        """Ask for a starting slot then upload each file consecutively."""
        def _on_slot(slot: int | None) -> None:
            if slot is not None:
                for i, pf in enumerate(patch_files):
                    internal_slot = slot - 1 + i  # user sees 1-based, pedal uses 0-based
                    log.debug("_upload_ask_slot._on_slot: slot=%r file=%s", slot + i, pf.name)
                    self._do_upload_send(pf, title, internal_slot)

        self.push_screen(SlotInputModal(), callback=_on_slot)

    @work(thread=True)
    def _do_upload_send(self, patch_file: Path, title: str, slot: int) -> None:
        """Background worker: upload an already-downloaded patch file to pedal."""
        from zoom_midi import ZoomDevice

        log.info("upload-send: file=%s title=%r slot=%d", patch_file, title, slot)

        self.call_from_thread(
            self.notify,
            f"Sending '{title}' to pedal slot {slot}…",
            title="Upload",
            timeout=8,
        )

        try:
            log.debug("upload-send: opening MIDI device…")
            with ZoomDevice(debug=self._debug_mode) as dev:
                log.debug("upload-send: device opened at %s", dev.device_path)
                name = dev.upload_patch(patch_file, slot)
                log.info("upload-send: success — name=%r slot=%d", name, slot)

            self.call_from_thread(
                self.notify,
                f"'{name}' → slot {slot}",
                title="Upload complete",
                timeout=8,
            )
        except Exception as exc:
            log.exception("upload-send: FAILED")
            self.call_from_thread(
                self.notify,
                f"Failed: {exc}",
                title="Upload error",
                severity="error",
                timeout=15,
            )

    # ── test-patch orchestration ──────────────────────────────────────────────

    def start_test_flow(self, url: str, patch: dict) -> None:
        """Download the patch then ask for a slot to test it on."""
        title = _field(patch, "title", "patch")
        log.info("start_test_flow: url=%s title=%r", url, title)
        self._do_test_download(url, patch)

    @work(thread=True)
    def _do_test_download(self, url: str, patch: dict) -> None:
        """Background worker: download patch then hand off to file-picker / slot prompt."""
        from scraper import ForumScraper

        title = _field(patch, "title", "patch")
        dest_dir = self._patch_dir(patch)

        self.call_from_thread(
            self.notify, f"Downloading \u2018{title}\u2019\u2026", title="Test patch", timeout=8,
        )

        scraper = ForumScraper()
        scraper.load_cookies()

        try:
            scraper.download_file(url, dest_dir, title=title)
            self._extract_archives(dest_dir)
            patch_files = self._find_patch_files(dest_dir)
            log.info("test-dl: found %d patch file(s)", len(patch_files))

            if not patch_files:
                contents = list(dest_dir.rglob("*"))
                self.call_from_thread(
                    self.notify,
                    f"No patch files found. Contents: {[c.name for c in contents[:10]]}",
                    title="Test patch error",
                    severity="error",
                    timeout=10,
                )
                return

            self.call_from_thread(self._test_pick_file, patch_files, title)

        except Exception as exc:
            log.exception("test-dl: FAILED")
            self.call_from_thread(
                self.notify,
                f"Download failed: {exc}",
                title="Test patch error",
                severity="error",
                timeout=15,
            )

    def _test_pick_file(self, patch_files: list[Path], title: str) -> None:
        """For one file go straight to pedal; for multiple show the file picker."""
        if len(patch_files) == 1:
            self._do_test_patch_load(patch_files[0], title)
            return

        def _on_choice(chosen: "list[Path] | None") -> None:
            if chosen:
                self._do_test_patch_load(chosen[0], title)

        self.push_screen(PatchFileChoiceModal(patch_files), callback=_on_choice)

    @work(thread=True)
    def _do_test_patch_load(self, patch_file: Path, title: str) -> None:
        """
        Background worker: enter editor mode, backup the current edit buffer,
        send the test patch to the edit buffer (volatile — no slot written),
        then leave the device open in editor mode so the pedal keeps playing it.
        """
        from zoom_midi import ZoomDevice, parse_patch_file

        self.call_from_thread(
            self.notify,
            f"Loading \u2018{title}\u2019 for testing\u2026",
            title="Test patch",
            timeout=8,
        )

        try:
            ptcf_data = parse_patch_file(patch_file)
            name_bytes = ptcf_data[26:37]
            patch_name = (
                bytes(b for b in name_bytes if 0x20 <= b <= 0x7E)
                .decode("ascii", errors="replace")
                .strip()
            ) or title

            backup_ptcf: bytes | None = None
            with ZoomDevice(debug=self._debug_mode) as dev:
                dev.editor_mode_on()
                try:
                    backup_ptcf = dev.read_current_patch()
                    log.debug("test-load: backup read OK (%d bytes)", len(backup_ptcf))
                except Exception as exc:
                    log.warning("test-load: backup read failed (restore unavailable): %s", exc)
                dev.send_patch_to_current(ptcf_data)
                # Intentionally leave editor mode ON so the test patch stays
                # active on the pedal while the user listens and decides.

            self.call_from_thread(
                self._test_show_confirm, backup_ptcf, patch_file, title, patch_name
            )

        except Exception as exc:
            log.exception("test-load: FAILED")
            self.call_from_thread(
                self.notify,
                f"Failed to load test patch: {exc}",
                title="Test patch error",
                severity="error",
                timeout=15,
            )

    def _test_show_confirm(
        self,
        backup_ptcf: "bytes | None",
        patch_file: Path,
        title: str,
        patch_name: str,
    ) -> None:
        """Main-thread: show keep/discard confirmation modal."""

        def _on_result(keep: bool) -> None:
            if keep:
                self._test_ask_slot_and_save(patch_file, title, backup_ptcf)
            else:
                self._do_test_patch_restore(backup_ptcf, title)

        self.push_screen(TestPatchConfirmModal(patch_name), callback=_on_result)

    def _test_ask_slot_and_save(
        self, patch_file: Path, title: str, backup_ptcf: "bytes | None"
    ) -> None:
        """Ask for a slot then permanently save; cancel restores the backup."""

        def _on_slot(slot: "int | None") -> None:
            if slot is not None:
                self._do_test_patch_save(patch_file, title, slot - 1)  # 1-based \u2192 0-based
            else:
                # User cancelled the slot dialog — treat as discard
                self._do_test_patch_restore(backup_ptcf, title)

        self.push_screen(SlotInputModal(), callback=_on_slot)

    @work(thread=True)
    def _do_test_patch_save(self, patch_file: Path, title: str, slot: int) -> None:
        """Background worker: permanently write the test patch to *slot* via PC mode."""
        from zoom_midi import ZoomDevice

        self.call_from_thread(
            self.notify,
            f"Saving \u2018{title}\u2019 to slot {slot + 1}\u2026",
            title="Test patch",
            timeout=8,
        )

        try:
            with ZoomDevice(debug=self._debug_mode) as dev:
                name = dev.upload_patch(patch_file, slot)
            self.call_from_thread(
                self.notify,
                f"\u2018{name}\u2019 saved to slot {slot + 1}.",
                title="Patch saved",
                timeout=8,
            )
        except Exception as exc:
            log.exception("test-save: FAILED")
            self.call_from_thread(
                self.notify,
                f"Save failed: {exc}",
                title="Test patch error",
                severity="error",
                timeout=15,
            )

    @work(thread=True)
    def _do_test_patch_restore(self, backup_ptcf: "bytes | None", title: str) -> None:
        """Background worker: restore the edit buffer to its pre-test state."""
        from zoom_midi import ZoomDevice

        if backup_ptcf is None:
            self.call_from_thread(
                self.notify,
                "Test discarded. (Backup unavailable \u2014 navigate away and back on the pedal to reload the original.)",
                title="Test patch",
                severity="warning",
                timeout=10,
            )
            return

        try:
            with ZoomDevice(debug=self._debug_mode) as dev:
                dev.editor_mode_on()
                dev.send_patch_to_current(backup_ptcf)
                # Do NOT call editor_mode_off() here.
                # The pedal auto-saves the edit buffer to the current slot when
                # the MIDI connection is closed without an explicit editor-off
                # command, which is exactly what we want: the backup overwrites
                # the test patch that was auto-saved when we loaded it.
            self.call_from_thread(
                self.notify,
                "Test discarded \u2014 original patch restored.",
                title="Test patch",
                timeout=5,
            )
        except Exception as exc:
            log.exception("test-restore: FAILED")
            self.call_from_thread(
                self.notify,
                f"Restore failed: {exc}",
                title="Test patch error",
                severity="error",
                timeout=15,
            )


# ── standalone entry ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    ZoomPatchBrowser().run()
