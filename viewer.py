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
    Static,
)

BASE_DIR     = Path(__file__).parent
INDEX_DIR    = BASE_DIR / "index"
DOWNLOAD_DIR = BASE_DIR / "downloads"
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
        title = _field(self.patch, "title", "patch")
        self.app.trigger_download(urls[0], title)

    def action_upload_to_pedal(self) -> None:
        urls = self.patch.get("download_urls", [])
        if not urls:
            self.notify("No download URL available for this patch.", severity="warning")
            return
        title = _field(self.patch, "title", "patch")
        log.debug("DetailScreen.action_upload_to_pedal: title=%r url=%s", title, urls[0])
        self.app.start_upload_flow(urls[0], title)


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
            yield Input(placeholder="Slot number (0–199)", id="slot-input", type="integer")
            yield Button("Upload", id="slot-ok", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        val = self.query_one("#slot-input", Input).value.strip()
        if val.isdigit():
            slot = int(val)
            if 0 <= slot <= 199:
                self.dismiss(slot)
                return
        self.notify("Enter a slot number between 0 and 199.", severity="warning")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.on_button_pressed(Button.Pressed(self.query_one("#slot-ok", Button)))

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
        Binding("r", "reload", "Reload index", show=False),
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
        title = _field(patch, "title", "patch")
        if len(urls) == 1:
            self.app.trigger_download(urls[0], title)
        else:
            def _on_pick(idx: int | None) -> None:
                if idx is not None:
                    self.app.trigger_download(urls[idx], title)

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
            self.app.start_upload_flow(urls[0], title)
        else:
            def _on_pick(idx: int | None) -> None:
                if idx is not None:
                    self.app.start_upload_flow(urls[idx], title)

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

    def trigger_download(self, url: str, title: str) -> None:
        """Called from any screen to start a background download."""
        self._do_download(url, title)

    @work(thread=True)
    def _do_download(self, url: str, title: str) -> None:
        """Background worker: download one patch file."""
        from scraper import ForumScraper

        scraper = ForumScraper()
        scraper.load_cookies()

        try:
            saved = scraper.download_file(url, DOWNLOAD_DIR, title=title)
            label = f"Extracted → {saved.name}/" if saved.is_dir() else f"Saved → {saved.name}"
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

    def start_upload_flow(self, url: str, title: str) -> None:
        """Prompt for slot number, then download + upload to pedal."""
        log.info("start_upload_flow: url=%s title=%r", url, title)

        def _on_slot(slot: int | None) -> None:
            log.debug("start_upload_flow._on_slot: slot=%r", slot)
            if slot is not None:
                self._do_upload(url, title, slot)

        self.push_screen(SlotInputModal(), callback=_on_slot)

    @work(thread=True)
    def _do_upload(self, url: str, title: str, slot: int) -> None:
        """Background worker: download patch then upload to pedal."""
        import tempfile
        from scraper import ForumScraper
        from zoom_midi import ZoomDevice

        log.info("upload: start — url=%s title=%r slot=%d", url, title, slot)

        self.call_from_thread(
            self.notify, f"Downloading '{title}'…", title="Upload", timeout=8,
        )

        scraper = ForumScraper()
        cookies_ok = scraper.load_cookies()
        log.debug("upload: cookies loaded = %s", cookies_ok)
        if not cookies_ok:
            log.warning("upload: no session cookies — download may fail if auth is required")

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                saved = scraper.download_file(url, Path(tmpdir), title=title)
                log.info("upload: downloaded → %s  (is_dir=%s)", saved, saved.is_dir())

                # Find the patch file (.zg* or extracted ToneLib.data)
                if saved.is_dir():
                    patch_files = list(saved.glob("*.zg*"))
                    if not patch_files:
                        patch_files = list(saved.glob("ToneLib.data"))
                    if not patch_files:
                        contents = list(saved.iterdir())
                        log.error("upload: no patch in extracted dir, contents: %s", contents)
                        raise RuntimeError(
                            f"No patch file found in downloaded archive. "
                            f"Contents: {[c.name for c in contents]}"
                        )
                    patch_file = patch_files[0]
                else:
                    patch_file = saved

                log.info("upload: patch_file = %s (%d bytes)", patch_file.name, patch_file.stat().st_size)

                self.call_from_thread(
                    self.notify,
                    f"Sending '{title}' to pedal slot {slot}…",
                    title="Upload",
                    timeout=8,
                )

                log.debug("upload: opening MIDI device…")
                with ZoomDevice(debug=self._debug_mode) as dev:
                    log.debug("upload: device opened at %s", dev.device_path)
                    name = dev.upload_patch(patch_file, slot)
                    log.info("upload: success — name=%r slot=%d", name, slot)

                self.call_from_thread(
                    self.notify,
                    f"'{name}' → slot {slot}",
                    title="Upload complete",
                    timeout=8,
                )
        except Exception as exc:
            log.exception("upload: FAILED")
            self.call_from_thread(
                self.notify,
                f"Failed: {exc}",
                title="Upload error",
                severity="error",
                timeout=15,
            )


# ── standalone entry ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    ZoomPatchBrowser().run()
