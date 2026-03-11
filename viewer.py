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
from textual.widget import Widget
from textual.widgets import (
    Button,
    Checkbox,
    ContentSwitcher,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    OptionList,
    Select,
    Static,
)

BASE_DIR        = Path(__file__).parent
APP_DIR         = Path.home() / ".zoomdownloader"
INDEX_DIR       = APP_DIR / "index"
DOWNLOAD_DIR    = APP_DIR / "downloads"
DEBUG_DIR       = BASE_DIR / "debug"
FAVOURITES_FILE = APP_DIR / "favourites.json"
PREFS_FILE      = APP_DIR / "prefs.json"

PEDAL_MODELS: list[tuple[str, str]] = [
    ("G3n",          "G3n"),
    ("G3Xn",         "G3Xn"),
    ("G3Xn + G3n",   "G3Xn+G3n"),
    ("G5n",          "G5n"),
]

# When filtering by a model key, which device-field substrings to accept.
# Keys are the *values* stored in prefs.json (second element of PEDAL_MODELS tuples).
_PEDAL_MATCH: dict[str, list[str]] = {
    "G3n":               ["g3n"],
    "G3Xn":              ["g3xn"],
    "G3Xn+G3n":          ["g3xn", "g3n"],
    "G3Xn + G3n (both)": ["g3xn", "g3n"],  # compat: old prefs written before fix
    "G5n":               ["g5n"],
}

log = logging.getLogger("zoomdownloader.browse")


# ── Preferences persistence ───────────────────────────────────────────────────

def load_prefs() -> dict:
    if not PREFS_FILE.exists():
        return {}
    try:
        return json.loads(PREFS_FILE.read_text())
    except Exception:
        return {}


def save_prefs(prefs: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    PREFS_FILE.write_text(json.dumps(prefs, indent=2))


# ── Preferences modal ────────────────────────────────────────────────────────

class PreferencesModal(ModalScreen[dict | None]):
    """Preferences dialog: pedal model, firmware version, and patch filter."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "dismiss_cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    PreferencesModal { align: center middle; }
    #prefs-box {
        width: 64;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    #prefs-box Label.section-label { margin-top: 1; color: $text-muted; }
    #prefs-box Label.heading { margin-bottom: 1; }
    #prefs-pedal  { margin-bottom: 1; }
    #prefs-firmware { margin-bottom: 1; }
    #prefs-filter { margin-bottom: 1; }
    #prefs-buttons { margin-top: 1; height: auto; }
    #prefs-save   { width: 1fr; }
    #prefs-cancel { width: 1fr; }
    """

    def compose(self) -> ComposeResult:
        with Container(id="prefs-box"):
            yield Label("[bold]Preferences[/bold]", markup=True, classes="heading")
            yield Label("My pedal model:", classes="section-label")
            yield Select(
                PEDAL_MODELS,
                id="prefs-pedal",
                allow_blank=True,
                prompt="(any / not set)",
            )
            yield Label("Firmware version:", classes="section-label")
            yield Input(
                placeholder="e.g. 2.10  (leave blank to match any)",
                id="prefs-firmware",
            )
            yield Checkbox(
                "Show only patches made for my pedal & firmware",
                id="prefs-filter",
            )
            with Horizontal(id="prefs-buttons"):
                yield Button("Save", id="prefs-save", variant="primary")
                yield Button("Cancel", id="prefs-cancel")

    def on_mount(self) -> None:
        prefs = load_prefs()
        pedal    = prefs.get("pedal", "")
        firmware = prefs.get("firmware", "")
        do_filter = prefs.get("filter", False)

        sel = self.query_one("#prefs-pedal", Select)
        if pedal:
            sel.value = pedal

        self.query_one("#prefs-firmware", Input).value = firmware
        self.query_one("#prefs-filter", Checkbox).value = do_filter
        self.query_one("#prefs-firmware", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "prefs-cancel":
            self.dismiss(None)
            return
        self._save_and_dismiss()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._save_and_dismiss()

    def _save_and_dismiss(self) -> None:
        sel = self.query_one("#prefs-pedal", Select)
        pedal    = "" if sel.value is Select.BLANK else str(sel.value)
        firmware = self.query_one("#prefs-firmware", Input).value.strip()
        do_filter = bool(self.query_one("#prefs-filter", Checkbox).value)
        prefs = {"pedal": pedal, "firmware": firmware, "filter": do_filter}
        save_prefs(prefs)
        self.dismiss(prefs)

    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)


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


# ── Favourites persistence ────────────────────────────────────────────────────

def load_favourites() -> list[dict]:
    """
    Load the favourites list from disk.

    Each element is either:
      - a standalone patch entry: {"id": ..., "title": ..., "url": ...}
      - a group:                  {"name": ..., "patches": [{id, title, url}, ...]}
    """
    if not FAVOURITES_FILE.exists():
        return []
    try:
        data = json.loads(FAVOURITES_FILE.read_text())
        if "items" in data:
            return data["items"]
        elif "groups" in data:
            # Old groups-only format — each group becomes a group item
            return data["groups"]
        elif "patches" in data:
            # Transitional flat format — all were standalone
            return data["patches"]
        return []
    except Exception:
        return []


def save_favourites(items: list[dict]) -> None:
    """Persist the favourites items list to disk."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    FAVOURITES_FILE.write_text(json.dumps({"items": items}, indent=2))


def get_all_fav_ids(items: list[dict]) -> set[str]:
    """Return all patch IDs referenced anywhere in the favourites list."""
    ids: set[str] = set()
    for item in items:
        if "id" in item:                          # standalone patch
            if item["id"]:
                ids.add(item["id"])
        elif "patches" in item:                   # group
            for p in item["patches"]:
                if p.get("id"):
                    ids.add(p["id"])
    return ids


def _item_is_patch(item: dict) -> bool:
    return "id" in item


def _item_is_group(item: dict) -> bool:
    return "name" in item


def _remove_patch_id_from_items(items: list[dict], patch_id: str) -> list[dict]:
    """Return a new items list with all references to patch_id stripped out."""
    result = []
    for item in items:
        if _item_is_patch(item):
            if item.get("id") != patch_id:
                result.append(item)
        elif _item_is_group(item):
            filtered = [p for p in item.get("patches", []) if p.get("id") != patch_id]
            result.append({**item, "patches": filtered})
        else:
            result.append(item)
    return result


# ── Detail screen ─────────────────────────────────────────────────────────────

class DetailScreen(Screen):
    """Full-screen detail view for a single patch."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape,q", "dismiss", "Back"),
        Binding("d", "download", "Download patch"),
        Binding("u", "upload_to_pedal", "Upload to pedal"),
        Binding("t", "test_patch", "Test patch"),
        Binding("f", "toggle_favourite", "Favourite"),
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
        groups = load_favourites()
        is_fav = p.get("_id", "") in get_all_fav_ids(groups)
        fav_tag = "  [bold yellow]★ FAVOURITE[/bold yellow]" if is_fav else "  [dim](f: favourite)[/dim]"
        yield Header(show_clock=False)

        with Vertical(id="detail-outer"):
            with ScrollableContainer(id="detail-scroll"):
                lines: list[str] = [
                    f"[bold cyan]Title:[/]         {_field(p, 'title')}{fav_tag}",
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

    def action_toggle_favourite(self) -> None:
        patch_id = self.patch.get("_id", "")
        if not patch_id:
            self.notify("Cannot favourite this patch (no ID).", severity="warning")
            return
        items = load_favourites()
        if patch_id in get_all_fav_ids(items):
            save_favourites(_remove_patch_id_from_items(items, patch_id))
            self.notify("Removed from favourites.")
        else:
            patch_entry = {
                "id": patch_id,
                "title": _field(self.patch, "title", "Untitled"),
                "url": (self.patch.get("download_urls") or [""])[0],
            }
            items.append(patch_entry)
            save_favourites(items)
            self.notify(f"Added '{patch_entry['title']}' to favourites.")


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

    def __init__(self, label: str = "Upload to pedal slot:") -> None:
        super().__init__()
        self._label = label

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
            yield Label(f"[bold]{self._label}[/bold]", markup=True)
            yield Input(placeholder="Slot 1–200  (blank = active slot)", id="slot-input", type="integer")
            yield Button("Upload", id="slot-ok", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        val = self.query_one("#slot-input", Input).value.strip()
        if val == "":
            self.dismiss(0)  # 0 = sentinel: use currently active slot
            return
        if val.isdigit():
            slot = int(val)
            if 1 <= slot <= 200:
                self.dismiss(slot)
                return
        self.notify("Enter a slot number between 1 and 200, or leave blank for the active slot.", severity="warning")

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


# ── Favourites modals ───────────────────────────────────────────────────────

class GroupNameModal(ModalScreen[str | None]):
    """Input a group name (create or rename)."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "dismiss_none", "Cancel"),
    ]

    DEFAULT_CSS = """
    GroupNameModal { align: center middle; }
    #gn-box { width: 50; height: auto; border: thick $primary; background: $surface; padding: 1 2; }
    #gn-box Label { margin-bottom: 1; }
    #gn-input { margin-bottom: 1; }
    """

    def __init__(self, initial: str = "") -> None:
        super().__init__()
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Container(id="gn-box"):
            yield Label("[bold]Group name:[/bold]", markup=True)
            yield Input(value=self._initial, placeholder="e.g. Song Name", id="gn-input")
            yield Button("OK", id="gn-ok", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#gn-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        val = self.query_one("#gn-input", Input).value.strip()
        if val:
            self.dismiss(val)
        else:
            self.notify("Please enter a name.", severity="warning")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.on_button_pressed(Button.Pressed(self.query_one("#gn-ok", Button)))

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class PickGroupModal(ModalScreen[str | None]):
    """
    Pick an existing group, create a new one, or (optionally) make standalone.

    Dismisses with:
      * a group name string  → add/move to that group
      * "__new__"           → caller should ask for a name then act
      * "__standalone__"    → caller should make the patch standalone (only when show_standalone=True)
      * None                → cancelled
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "dismiss_none", "Cancel"),
    ]

    DEFAULT_CSS = """
    PickGroupModal { align: center middle; }
    #pg-box { width: 60; height: auto; max-height: 80%; border: thick $primary; background: $surface; padding: 1 2; }
    #pg-box Label { margin-bottom: 1; }
    #pg-list { height: auto; max-height: 40vh; margin-bottom: 1; }
    """

    def __init__(
        self,
        groups: list[dict],
        title: str = "Choose group:",
        exclude: str | None = None,
        show_standalone: bool = False,
    ) -> None:
        super().__init__()
        self._groups = [g for g in groups if g.get("name") != exclude]
        self._title = title
        self._show_standalone = show_standalone

    def compose(self) -> ComposeResult:
        with Container(id="pg-box"):
            yield Label(f"[bold]{self._title}[/bold]", markup=True)
            names = [g["name"] for g in self._groups]
            if names:
                yield OptionList(*names, id="pg-list")
            yield Button("+ New group", id="pg-new", variant="default")
            if self._show_standalone:
                yield Button("Make standalone (no group)", id="pg-standalone", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "pg-new":
            self.dismiss("__new__")
        elif event.button.id == "pg-standalone":
            self.dismiss("__standalone__")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self._groups[event.option_index]["name"])

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


# ── Favourites list widgets ───────────────────────────────────────────────────

class GroupHeaderItem(ListItem):
    """Group header row in the favourites list."""

    DEFAULT_CSS = """
    GroupHeaderItem { background: $panel; padding: 0; }
    GroupHeaderItem > Static { padding: 0 1; width: 100%; }
    """

    def __init__(self, group: dict, item_idx: int) -> None:
        super().__init__()
        self.group_data = group
        self.item_idx = item_idx

    def compose(self) -> ComposeResult:
        name  = self.group_data.get("name", "?")
        n     = len(self.group_data.get("patches", []))
        noun  = "patch" if n == 1 else "patches"
        yield Static(
            f"[bold cyan]\u25b6  {name}[/bold cyan]  [dim]({n} {noun})[/dim]",
            markup=True,
        )


class PatchFavItem(ListItem):
    """
    Patch row in the favourites list.

    item_idx  – index in FavouritesPanel._items
    patch_idx – None when standalone; index inside group's patches list when grouped
    """

    DEFAULT_CSS = """
    PatchFavItem > Static { padding: 0 1; }
    PatchFavItem.grouped > Static { padding: 0 3; }
    """

    def __init__(self, patch: dict, item_idx: int, patch_idx: int | None) -> None:
        super().__init__(classes="grouped" if patch_idx is not None else "")
        self.patch_data = patch
        self.item_idx  = item_idx
        self.patch_idx = patch_idx

    def compose(self) -> ComposeResult:
        prefix  = "\u00b7" if self.patch_idx is not None else "\u2022"
        title   = self.patch_data.get("title", "Untitled")
        device  = self.patch_data.get("device", "").strip()
        fw      = self.patch_data.get("firmware", "").strip()
        meta    = "  ".join(part for part in [device, (f"fw {fw}" if fw else "")] if part)
        line    = f"{prefix} {title}"
        if meta:
            line += f"  [dim]{meta}[/dim]"
        yield Static(line, markup=True)


# ── Favourites panel ──────────────────────────────────────────────────────────

class FavouritesPanel(Widget):
    """
    Hierarchical favourites: standalone patches and/or named groups.
    Pressing f anywhere adds a patch as a standalone entry, no dialog.
    Groups can be created / renamed / reordered inside the panel.
    """

    CAN_FOCUS = True

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("enter",      "view_detail",     "Details",         show=True),
        Binding("shift+up",   "move_up",        "Move up"),
        Binding("shift+down", "move_down",       "Move down"),
        Binding("f",          "unfavourite",     "Remove fav"),
        Binding("delete",     "unfavourite",     "Remove",          show=False),
        Binding("n",          "new_group",       "New group"),
        Binding("r",          "rename_group",    "Rename group"),
        Binding("m",          "move_to_group",   "Move to group"),
        Binding("c",          "copy_to_group",   "Copy to group"),
        Binding("u",          "upload_to_pedal", "Upload to pedal"),
    ]

    DEFAULT_CSS = """
    FavouritesPanel { height: 1fr; layout: vertical; }
    #fav-list { height: 1fr; }
    #fav-empty {
        height: 1fr; content-align: center middle;
        color: $text-muted; padding: 2 4;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._items: list[dict] = []

    def compose(self) -> ComposeResult:
        yield ListView(id="fav-list")
        yield Static(
            "No favourites yet.\n\n"
            "Press [bold]f[/bold] on any patch to instantly add it here.\n"
            "Press [bold]n[/bold] to create a named group.",
            id="fav-empty",
            markup=True,
        )

    def on_mount(self) -> None:
        self.reload()

    # ── data helpers ─────────────────────────────────────────────────────────

    def reload(self) -> None:
        self._items = load_favourites()
        self._enrich_items(self._items)
        self._refresh_list()

    @staticmethod
    def _enrich_items(items: list[dict]) -> None:
        """Fill in missing device/firmware on fav entries by reading the index."""
        def _enrich_patch(entry: dict) -> None:
            if entry.get("device") and entry.get("firmware"):
                return  # already populated
            pid = entry.get("id", "")
            if not pid:
                return
            p = INDEX_DIR / f"{pid}.json"
            if not p.exists():
                return
            try:
                data = json.loads(p.read_text())
                if not entry.get("device"):
                    entry["device"] = data.get("device", "").strip()
                if not entry.get("firmware"):
                    entry["firmware"] = data.get("firmware", "").strip()
            except Exception:
                pass

        for item in items:
            if _item_is_patch(item):
                _enrich_patch(item)
            elif _item_is_group(item):
                for p in item.get("patches", []):
                    _enrich_patch(p)

    def _total_items(self) -> int:
        total = 0
        for item in self._items:
            total += 1
            if _item_is_group(item):
                total += len(item.get("patches", []))
        return total

    def _flat_index_of(self, item_idx: int, patch_idx: int | None = None) -> int:
        idx = 0
        for i, item in enumerate(self._items):
            if i == item_idx and patch_idx is None:
                return idx
            idx += 1
            if _item_is_group(item):
                for pi in range(len(item.get("patches", []))):
                    if i == item_idx and pi == patch_idx:
                        return idx
                    idx += 1
        return 0

    def _refresh_list(self, restore_index: int | None = None) -> None:
        lv        = self.query_one("#fav-list", ListView)
        empty_lbl = self.query_one("#fav-empty", Static)
        if not self._items:
            lv.display = False
            empty_lbl.display = True
            return
        lv.display = True
        empty_lbl.display = False
        lv.clear()
        for i, item in enumerate(self._items):
            if _item_is_patch(item):
                lv.append(PatchFavItem(item, i, None))
            else:  # group
                lv.append(GroupHeaderItem(item, i))
                for pi, p in enumerate(item.get("patches", [])):
                    lv.append(PatchFavItem(p, i, pi))
        if restore_index is not None:
            _idx = restore_index

            def _restore() -> None:
                total = self._total_items()
                if total > 0:
                    lv.index = max(0, min(_idx, total - 1))

            self.call_after_refresh(_restore)

    def _save_and_refresh(self, restore_index: int | None = None) -> None:
        save_favourites(self._items)
        self._refresh_list(restore_index)

    def _current_item(self) -> ListItem | None:
        return self.query_one("#fav-list", ListView).highlighted_child

    def _groups_list(self) -> list[dict]:
        return [item for item in self._items if _item_is_group(item)]

    # ── reorder ──────────────────────────────────────────────────────────────

    def action_move_up(self) -> None:
        row = self._current_item()
        if isinstance(row, GroupHeaderItem):
            ii = row.item_idx
            if ii <= 0:
                return
            self._items[ii], self._items[ii - 1] = self._items[ii - 1], self._items[ii]
            self._save_and_refresh(self._flat_index_of(ii - 1))
        elif isinstance(row, PatchFavItem):
            ii, pi = row.item_idx, row.patch_idx
            if pi is None:  # standalone
                if ii <= 0:
                    return
                prev = self._items[ii - 1]
                if _item_is_group(prev):
                    # Move standalone patch into the END of the group above
                    patch = self._items.pop(ii)
                    prev.setdefault("patches", []).append(patch)
                    new_pi = len(prev["patches"]) - 1
                    self._save_and_refresh(self._flat_index_of(ii - 1, new_pi))
                else:
                    self._items[ii], self._items[ii - 1] = self._items[ii - 1], self._items[ii]
                    self._save_and_refresh(self._flat_index_of(ii - 1))
            else:  # inside group
                patches = self._items[ii].setdefault("patches", [])
                if pi > 0:
                    patches[pi], patches[pi - 1] = patches[pi - 1], patches[pi]
                    self._save_and_refresh(self._flat_index_of(ii, pi - 1))
                else:
                    # First patch in group → pop out as standalone above the group header
                    patch = patches.pop(0)
                    self._items.insert(ii, patch)
                    self._save_and_refresh(self._flat_index_of(ii))

    def action_move_down(self) -> None:
        row = self._current_item()
        if isinstance(row, GroupHeaderItem):
            ii = row.item_idx
            if ii >= len(self._items) - 1:
                return
            self._items[ii], self._items[ii + 1] = self._items[ii + 1], self._items[ii]
            self._save_and_refresh(self._flat_index_of(ii + 1))
        elif isinstance(row, PatchFavItem):
            ii, pi = row.item_idx, row.patch_idx
            if pi is None:  # standalone
                if ii >= len(self._items) - 1:
                    return
                nxt = self._items[ii + 1]
                if _item_is_group(nxt):
                    # Move standalone patch into the START of the group below
                    patch = self._items.pop(ii)
                    nxt.setdefault("patches", []).insert(0, patch)
                    self._save_and_refresh(self._flat_index_of(ii, 0))
                else:
                    self._items[ii], self._items[ii + 1] = self._items[ii + 1], self._items[ii]
                    self._save_and_refresh(self._flat_index_of(ii + 1))
            else:  # inside group
                patches = self._items[ii].setdefault("patches", [])
                if pi < len(patches) - 1:
                    patches[pi], patches[pi + 1] = patches[pi + 1], patches[pi]
                    self._save_and_refresh(self._flat_index_of(ii, pi + 1))
                else:
                    # Last patch in group → pop out as standalone below the group
                    patch = patches.pop(pi)
                    insert_at = ii + 1
                    self._items.insert(insert_at, patch)
                    self._save_and_refresh(self._flat_index_of(insert_at))

    # ── removing ─────────────────────────────────────────────────────────────

    def action_unfavourite(self) -> None:
        row = self._current_item()
        if isinstance(row, GroupHeaderItem):
            ii   = row.item_idx
            name = self._items[ii].get("name", "group")
            flat = self._flat_index_of(ii)
            self._items.pop(ii)
            total = self._total_items()
            restore = min(flat, total - 1) if total > 0 else 0
            self._save_and_refresh(restore)
            self.notify(f"Deleted group \u2018{name}\u2019.")
        elif isinstance(row, PatchFavItem):
            ii, pi = row.item_idx, row.patch_idx
            if pi is None:
                title = self._items[ii].get("title", "patch")
                flat  = self._flat_index_of(ii)
                self._items.pop(ii)
            else:
                title = self._items[ii]["patches"][pi].get("title", "patch")
                flat  = self._flat_index_of(ii, pi)
                self._items[ii]["patches"].pop(pi)
            total   = self._total_items()
            restore = min(flat, total - 1) if total > 0 else 0
            self._save_and_refresh(restore)
            self.notify(f"Removed \u2018{title}\u2019 from favourites.")
        else:
            self.notify("Select a patch or group to remove.", severity="warning")

    # ── group management ──────────────────────────────────────────────────────

    def action_new_group(self) -> None:
        def _on_name(name: str | None) -> None:
            if name:
                self._items.append({"name": name, "patches": []})
                self._save_and_refresh(self._total_items() - 1)
        self.app.push_screen(GroupNameModal(), callback=_on_name)

    def action_rename_group(self) -> None:
        row = self._current_item()
        if isinstance(row, GroupHeaderItem):
            ii   = row.item_idx
            flat = self._flat_index_of(ii)
        elif isinstance(row, PatchFavItem) and row.patch_idx is not None:
            ii   = row.item_idx
            flat = self._flat_index_of(ii, row.patch_idx)
        else:
            self.notify("Select a group (header or its patch) to rename.", severity="warning")
            return
        current_name = self._items[ii].get("name", "")

        def _on_name(name: str | None) -> None:
            if name:
                self._items[ii]["name"] = name
                self._save_and_refresh(flat)

        self.app.push_screen(GroupNameModal(initial=current_name), callback=_on_name)

    # ── move / copy ───────────────────────────────────────────────────────────

    def action_move_to_group(self) -> None:
        row = self._current_item()
        if not isinstance(row, PatchFavItem):
            self.notify("Select a patch to move.", severity="warning")
            return
        ii, pi = row.item_idx, row.patch_idx
        patch        = dict(self._items[ii] if pi is None else self._items[ii]["patches"][pi])
        current_name = self._items[ii].get("name") if pi is not None else None

        def _do_move(target_name: str) -> None:
            # Remove from current location
            if pi is None:
                self._items.pop(ii)
            else:
                self._items[ii]["patches"].pop(pi)
            if target_name == "__standalone__":
                # Insert just after where the group was
                insert_at = min(ii + 1, len(self._items))
                self._items.insert(insert_at, patch)
                self._save_and_refresh(self._flat_index_of(insert_at))
                return
            target = next((g for g in self._items if _item_is_group(g) and g["name"] == target_name), None)
            if target is None:
                self._items.append({"name": target_name, "patches": [patch]})
            else:
                target.setdefault("patches", []).append(patch)
            t_ii = next(i for i, g in enumerate(self._items) if _item_is_group(g) and g["name"] == target_name)
            t_pi = len(self._items[t_ii]["patches"]) - 1
            self._save_and_refresh(self._flat_index_of(t_ii, t_pi))
            self.notify(f"Moved to \u2018{target_name}\u2019.")

        def _on_pick(choice: str | None) -> None:
            if choice == "__new__":
                def _on_name(name: str | None) -> None:
                    if name:
                        _do_move(name)
                self.app.push_screen(GroupNameModal(), callback=_on_name)
            elif choice:
                _do_move(choice)

        self.app.push_screen(
            PickGroupModal(
                groups=self._groups_list(), title="Move patch to:",
                exclude=current_name, show_standalone=(pi is not None),
            ),
            callback=_on_pick,
        )

    def action_copy_to_group(self) -> None:
        row = self._current_item()
        if not isinstance(row, PatchFavItem):
            self.notify("Select a patch to copy.", severity="warning")
            return
        ii, pi  = row.item_idx, row.patch_idx
        patch   = dict(self._items[ii] if pi is None else self._items[ii]["patches"][pi])
        flat    = self._flat_index_of(ii, pi)

        def _do_copy(target_name: str) -> None:
            if target_name == "__standalone__":
                self._items.append(dict(patch))
                self._save_and_refresh(self._total_items() - 1)
                self.notify(f"Copied \u2018{patch.get('title', '?')}\u2019 as standalone.")
                return
            target = next((g for g in self._items if _item_is_group(g) and g["name"] == target_name), None)
            if target is None:
                self._items.append({"name": target_name, "patches": [dict(patch)]})
            else:
                target.setdefault("patches", []).append(dict(patch))
            self._save_and_refresh(flat)
            self.notify(f"Copied to \u2018{target_name}\u2019.")

        def _on_pick(choice: str | None) -> None:
            if choice == "__new__":
                def _on_name(name: str | None) -> None:
                    if name:
                        _do_copy(name)
                self.app.push_screen(GroupNameModal(), callback=_on_name)
            elif choice:
                _do_copy(choice)

        self.app.push_screen(
            PickGroupModal(
                groups=self._groups_list(), title="Copy patch to:", show_standalone=True,
            ),
            callback=_on_pick,
        )

    # ── upload ────────────────────────────────────────────────────────────────

    def action_upload_to_pedal(self) -> None:
        total = sum(
            1 if _item_is_patch(item) else len(item.get("patches", []))
            for item in self._items
        )
        if total == 0:
            self.notify("No patches in favourites to upload.", severity="warning")
            return
        has_groups = any(_item_is_group(item) and item.get("patches") for item in self._items)
        hint = " (groups are bank-aligned)" if has_groups else ""
        label = f"Upload {total} patch(es) starting at slot{hint}:"

        def _on_slot(slot: int | None) -> None:
            if slot is None:
                return
            if slot == 0:
                self.notify("Please enter a specific starting slot for batch upload.", severity="warning")
                return
            self.app.upload_favourites_to_pedal(self._items, slot - 1)  # 1→0-based

        self.app.push_screen(SlotInputModal(label=label), callback=_on_slot)

    # ── detail navigation ─────────────────────────────────────────────────────

    def action_view_detail(self) -> None:
        row = self._current_item()
        if isinstance(row, PatchFavItem):
            patch = self.app.find_patch_by_id(row.patch_data.get("id", ""))
            if patch:
                self.app.push_screen(DetailScreen(patch))
            else:
                self.notify(
                    f"Patch \u2018{row.patch_data.get('title', '?')}\u2019 not in local index.",
                    severity="warning",
                )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, PatchFavItem):
            self.action_view_detail()

    # ── public API ────────────────────────────────────────────────────────────

    def add_patch(self, patch: dict) -> None:
        """Add as a standalone favourite instantly — no dialog."""
        patch_entry = {
            "id":       patch.get("_id", ""),
            "title":    _field(patch, "title", "Untitled"),
            "url":      (patch.get("download_urls") or [""])[0],
            "device":   patch.get("device", "").strip(),
            "firmware": patch.get("firmware", "").strip(),
        }
        if patch_entry["id"] and patch_entry["id"] in get_all_fav_ids(self._items):
            self.notify("Already in favourites.", severity="warning")
            return
        self._items.append(patch_entry)
        self._save_and_refresh(self._total_items() - 1)
        self.notify(f"Added \u2018{patch_entry['title']}\u2019 to favourites.")


# ── Main browser screen ───────────────────────────────────────────────────────

class BrowserScreen(Screen):
    """Main file-list screen with search."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q",          "quit_app",           "Quit"),
        Binding("tab",        "switch_tab",         "Switch tab",        show=True, priority=True),
        Binding("/",          "focus_search",       "Search",            show=True),
        Binding("escape",     "clear_search",       "Clear search",      show=False),
        Binding("enter",      "view_detail",        "Details",           show=True),
        Binding("d",          "download_selected",  "Download",          show=True),
        Binding("u",          "upload_selected",    "Upload to pedal",   show=True),
        Binding("t",          "test_selected",      "Test patch",        show=True),
        Binding("f",          "favourite_selected", "Favourite",         show=True),
        Binding("r",          "reload",             "Reload index",      show=True),
        Binding("p",          "preferences",        "Preferences",       show=True),
    ]

    DEFAULT_CSS = """
    BrowserScreen { layout: vertical; }
    #tab-bar {
        height: 1; padding: 0 1;
        background: $panel-darken-1;
    }
    ContentSwitcher { height: 1fr; }
    #view-patches  { height: 1fr; layout: vertical; }
    #view-favourites { height: 1fr; }
    #search-row {
        height: 3; padding: 0 1;
        background: $panel;
    }
    #search-input { width: 1fr; }
    #patch-table  { height: 1fr; }
    #status-bar {
        height: 1; padding: 0 1;
        background: $panel; color: $text-muted;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.all_patches: list[dict] = []
        self.filtered_patches: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static("", id="tab-bar")
        with ContentSwitcher(initial="view-patches", id="main-switcher"):
            with Vertical(id="view-patches"):
                with Horizontal(id="search-row"):
                    yield Input(
                        placeholder="  Type to filter  (press / to focus, Esc to clear)",
                        id="search-input",
                    )
                yield DataTable(id="patch-table", cursor_type="row", zebra_stripes=True)
                yield Static("", id="status-bar")
            with Vertical(id="view-favourites"):
                yield FavouritesPanel()
        yield Footer()

    def on_mount(self) -> None:
        self._build_columns()
        self.reload_data()
        self.query_one("#patch-table", DataTable).focus()
        self._update_tab_bar()

    def on_screen_resume(self) -> None:
        """Refresh the \u2605 column whenever we return to this screen."""
        self._refresh_table()

    def _build_columns(self) -> None:
        t = self.query_one("#patch-table", DataTable)
        t.add_columns("Title", "Name on device", "Device", "Firmware", "Optimized for", "DL", "★")

    def reload_data(self) -> None:
        self.all_patches = load_patches()
        q = self.query_one("#search-input", Input).value
        self._refilter(q)

    def _refilter(self, query: str = "") -> None:
        """Apply prefs filter then text search; update filtered_patches and table."""
        prefs = load_prefs()
        if prefs.get("filter"):
            pedal    = prefs.get("pedal", "").strip()
            fw       = prefs.get("firmware", "").lower().strip()
            keywords = _PEDAL_MATCH.get(pedal, [pedal.lower()] if pedal else [])
            base = [
                p for p in self.all_patches
                if (not keywords or any(kw in p.get("device", "").lower() for kw in keywords))
                and (not fw      or fw in p.get("firmware", "").lower())
            ]
        else:
            base = self.all_patches

        q = query.lower().strip()
        if not q:
            self.filtered_patches = list(base)
        else:
            self.filtered_patches = [
                p for p in base
                if q in " ".join([
                    p.get("title", ""),
                    p.get("device", ""),
                    p.get("firmware", ""),
                    p.get("optimized_for", ""),
                    p.get("name_on_device", ""),
                    p.get("patch_comments", ""),
                ]).lower()
            ]
        self._refresh_table()

    def _refresh_table(self) -> None:
        fav_ids = get_all_fav_ids(load_favourites())
        t = self.query_one("#patch-table", DataTable)
        saved_row = t.cursor_row  # preserve cursor before rebuild
        t.clear()
        for i, p in enumerate(self.filtered_patches):
            dl_count = len(p.get("download_urls", []))
            dl_cell = Text(str(dl_count), style="green" if dl_count else "red")
            star = Text("★", style="bold yellow") if p.get("_id", "") in fav_ids else Text("")
            t.add_row(
                _field(p, "title"),
                _field(p, "name_on_device"),
                _field(p, "device"),
                _field(p, "firmware"),
                _field(p, "optimized_for"),
                dl_cell,
                star,
                key=str(i),
            )
        if saved_row is not None and self.filtered_patches:
            _r = saved_row
            def _restore(row: int = _r) -> None:
                n = len(self.filtered_patches)
                t.move_cursor(row=max(0, min(row, n - 1)), animate=False)
            self.call_after_refresh(_restore)
        status = self.query_one("#status-bar", Static)
        total = len(self.all_patches)
        shown = len(self.filtered_patches)
        prefs = load_prefs()
        filter_active = prefs.get("filter", False)
        filter_tag = ""
        if filter_active:
            pedal = prefs.get("pedal", "") or "any"
            fw    = prefs.get("firmware", "") or "any"
            filter_tag = f"  [dim cyan][filter: {pedal} / fw {fw}][/dim cyan]"
        if total == 0:
            status.update(
                "[yellow]No patches indexed yet. Run:[/yellow] [bold]python main.py scrape[/bold]"
            )
        elif shown == total:
            status.update(f"{total} patches{filter_tag}")
        else:
            status.update(f"{shown} of {total} patches match{filter_tag}")

    # ── tab switching ─────────────────────────────────────────────────────────

    def _update_tab_bar(self) -> None:
        cs = self.query_one("#main-switcher", ContentSwitcher)
        if cs.current == "view-patches":
            text = (
                "[bold reverse] All Patches [/bold reverse]"
                "  [dim] \u2605 Favourites [/dim]"
                "  [dim]Tab: switch[/dim]"
            )
        else:
            text = (
                "[dim] All Patches [/dim]"
                "  [bold reverse] \u2605 Favourites [/bold reverse]"
                "  [dim]Tab: switch[/dim]"
            )
        self.query_one("#tab-bar", Static).update(text)

    def action_switch_tab(self) -> None:
        cs  = self.query_one("#main-switcher", ContentSwitcher)
        fav = self.query_one(FavouritesPanel)
        if cs.current == "view-patches":
            cs.current = "view-favourites"
            fav.reload()
            if fav._items:
                fav.query_one(ListView).focus()
            else:
                fav.focus()
        else:
            cs.current = "view-patches"
            self._refresh_table()
            self.query_one("#patch-table", DataTable).focus()
        self._update_tab_bar()

    def action_favourite_selected(self) -> None:
        patch = self._selected_patch()
        if patch is None:
            return
        patch_id = patch.get("_id", "")
        if not patch_id:
            self.notify("Cannot favourite this patch (no ID).", severity="warning")
            return
        items = load_favourites()
        if patch_id in get_all_fav_ids(items):
            save_favourites(_remove_patch_id_from_items(items, patch_id))
            self._refresh_table()
            self.notify("Removed from favourites.")
        else:
            fav = self.query_one(FavouritesPanel)
            fav.add_patch(patch)
            self._refresh_table()

    # ── search ───────────────────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "search-input":
            return
        self._refilter(event.value)

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

    def action_preferences(self) -> None:
        def _on_close(prefs: dict | None) -> None:
            if prefs is not None:
                q = self.query_one("#search-input", Input).value
                self._refilter(q)
                pedal = prefs.get("pedal", "") or "any"
                fw    = prefs.get("firmware", "") or "any"
                if prefs.get("filter"):
                    self.notify(
                        f"Filtering to {pedal} / firmware {fw}.",
                        title="Preferences saved",
                    )
                else:
                    self.notify("Showing all patches.", title="Preferences saved")

        self.app.push_screen(PreferencesModal(), callback=_on_close)

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
            if slot is None:
                return  # cancelled
            if slot == 0:  # sentinel: upload to the currently active pedal slot
                self._do_upload_send_current(patch_files, title)
            else:
                for i, pf in enumerate(patch_files):
                    internal_slot = slot - 1 + i  # user sees 1-based, pedal uses 0-based
                    log.debug("_upload_ask_slot._on_slot: slot=%r file=%s", slot + i, pf.name)
                    self._do_upload_send(pf, title, internal_slot)

        self.push_screen(SlotInputModal(), callback=_on_slot)

    @work(thread=True)
    def _do_upload_send_current(self, patch_files: list[Path], title: str) -> None:
        """Background worker: resolve the active pedal slot then upload patch(es) starting there."""
        from zoom_midi import ZoomDevice

        self.call_from_thread(self.notify, "Querying active slot…", title="Upload", timeout=4)

        try:
            with ZoomDevice(debug=self._debug_mode) as dev:
                current_slot = dev.get_current_slot()
        except Exception as exc:
            log.exception("_do_upload_send_current: device query failed")
            self.call_from_thread(
                self.notify,
                f"Failed to query pedal: {exc}",
                title="Upload error",
                severity="error",
                timeout=15,
            )
            return

        if current_slot is None:
            self.call_from_thread(
                self.notify,
                "Could not determine the active slot from the pedal.",
                title="Upload error",
                severity="error",
                timeout=10,
            )
            return

        log.info("_do_upload_send_current: active slot = %d", current_slot)
        for i, pf in enumerate(patch_files):
            self._do_upload_send(pf, title, current_slot + i)

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

    # ── favourites upload ─────────────────────────────────────────────────────

    def find_patch_by_id(self, patch_id: str) -> dict | None:
        """Look up a patch by ID from the local index."""
        p = INDEX_DIR / f"{patch_id}.json"
        if p.exists():
            try:
                data = json.loads(p.read_text())
                data["_file"] = p.name
                data["_id"] = p.stem
                return data
            except Exception:
                pass
        return None

    def upload_favourites_to_pedal(self, items: list[dict], start_slot: int) -> None:
        """
        Compute the upload slot layout and fire the background worker.
        - Standalone patches fill sequential slots.
        - Groups are bank-aligned (3 slots per bank, with padding).
        """
        BANK = 3
        layout: list[tuple[int, dict | None]] = []
        slot = start_slot

        for item in items:
            if _item_is_patch(item):
                layout.append((slot, item))
                slot += 1
            else:  # group
                patches = item.get("patches", [])
                if not patches:
                    continue
                # Advance to the next bank boundary
                if slot % BANK != 0:
                    slot += BANK - (slot % BANK)
                n            = len(patches)
                banks_needed = (n + BANK - 1) // BANK
                for i, p in enumerate(patches):
                    layout.append((slot + i, p))
                leftover = n % BANK
                if leftover:
                    for j in range(BANK - leftover):
                        layout.append((slot + n + j, None))  # padding
                slot += banks_needed * BANK

        if not any(p is not None for _, p in layout):
            self.notify("No patches to upload.", severity="warning")
            return

        self._do_upload_favourites(layout)

    @work(thread=True)
    def _do_upload_favourites(self, layout: list[tuple[int, dict | None]]) -> None:
        """Background worker: download all patches then upload in ONE pc_mode session."""
        from scraper import ForumScraper
        from zoom_midi import ZoomDevice, parse_patch_file, clamp_ptcf_effects_for_g3xn

        actual        = [(s, p) for s, p in layout if p is not None]
        padding_slots = [s for s, p in layout if p is None]
        total  = len(actual)
        done   = 0
        errors: list[str] = []

        scraper = ForumScraper()
        scraper.load_cookies()

        # ── Phase 1: resolve / download all patch files ───────────────────────────
        # Do all network I/O BEFORE opening the MIDI device so the pedal is
        # never left waiting while we hit the network.
        self.call_from_thread(
            self.notify,
            f"Preparing {total} patch(es) for upload…",
            title="Upload Favourites",
            timeout=10,
        )
        resolved: list[tuple[int, object, str]] = []  # (slot, local_path, title)
        for slot, patch_entry in actual:
            title = patch_entry.get("title", "patch")
            url   = patch_entry.get("url",   "")
            pid   = patch_entry.get("id",    "")
            patch_file = self._find_or_download_fav_patch(scraper, pid, url, title)
            if patch_file is None:
                errors.append(f"\u2022 {title}: could not find or download patch file")
            else:
                resolved.append((slot, patch_file, title))

        if not resolved:
            self.call_from_thread(
                self.notify,
                "No patches could be prepared for upload.\n" + "\n".join(errors),
                title="Upload Favourites",
                severity="warning",
                timeout=20,
            )
            return

        # ── Phase 2: single pc_mode session for all writes ────────────────────────
        # Opening/closing PC mode around every individual patch upload is what
        # causes pedal crashes: each pc_mode_off triggers a firmware reload
        # cycle, and hammering it repeatedly (especially with patches that have
        # unfamiliar effect modules) locks up the pedal.  One session = one
        # firmware reload at the end.
        try:
            with ZoomDevice(debug=self._debug_mode) as dev:
                dev.pc_mode_on()
                try:
                    _count, psize, bsize = dev.patch_check()
                    n = len(resolved)
                    blank_template: bytes | None = None
                    for i, (slot, patch_file, title) in enumerate(resolved):
                        self.call_from_thread(
                            self.notify,
                            f"Uploading \u2018{title}\u2019 \u2192 slot {slot + 1}  ({i + 1}/{n})\u2026",
                            title="Upload Favourites",
                            timeout=6,
                        )
                        try:
                            ptcf_data = parse_patch_file(patch_file)
                            ptcf_data = clamp_ptcf_effects_for_g3xn(ptcf_data)
                            ptcf_len  = len(ptcf_data)
                            if ptcf_len < psize:
                                ptcf_data += b"\x00" * (psize - ptcf_len)
                            elif ptcf_len > psize:
                                raise ValueError(
                                    f"PTCF is {ptcf_len} bytes but pedal expects "
                                    f"{psize} \u2014 cannot upload."
                                )
                            name_bytes = ptcf_data[26:37]
                            name = bytes(
                                b for b in name_bytes if 0x20 <= b <= 0x7E
                            ).decode("ascii", errors="replace").strip()
                            dev.write_patch_to_slot(slot, ptcf_data, bsize)
                            done += 1
                            log.info("fav-upload: \u2018%s\u2019 \u2192 slot %d", name, slot + 1)
                            if blank_template is None:
                                blank_template = ptcf_data  # save as template for padding slots
                        except Exception as exc:
                            log.exception("fav-upload: FAILED for \u2018%s\u2019", title)
                            errors.append(f"\u2022 {title}: {exc}")

                    # Clear padding slots (group bank remainder) using the first
                    # successfully-uploaded PTCF as a structural template, with
                    # its name blanked.  We cannot read slots back from the device
                    # in PC mode (cmd 0x09 returns only a short ACK in that mode).
                    for slot in padding_slots:
                        if blank_template is None:
                            log.warning("fav-upload: no template PTCF — cannot clear padding slot %d", slot + 1)
                            errors.append(f"\u2022 Slot {slot + 1} (padding): no template patch available")
                            continue
                        try:
                            blank = bytearray(blank_template)
                            blank[26:37] = b"           "  # 11-byte name field → spaces
                            dev.write_patch_to_slot(slot, bytes(blank), bsize)
                            log.info("fav-upload: cleared padding slot %d", slot + 1)
                        except Exception as exc:
                            log.exception("fav-upload: FAILED to clear padding slot %d", slot + 1)
                            errors.append(f"\u2022 Slot {slot + 1} (padding): {exc}")
                finally:
                    dev.pc_mode_off()
        except Exception as exc:
            log.exception("fav-upload: MIDI session failed")
            errors.append(f"\u2022 MIDI error: {exc}")

        if errors:
            self.call_from_thread(
                self.notify,
                f"Uploaded {done}/{total}.\n" + "\n".join(errors),
                title="Upload Favourites",
                severity="warning",
                timeout=20,
            )
        else:
            self.call_from_thread(
                self.notify,
                f"Uploaded {done}/{total} patches successfully.",
                title="Upload Favourites complete",
                timeout=8,
            )

    @staticmethod
    def _find_or_download_fav_patch(scraper, patch_id: str, url: str, title: str) -> "Path | None":
        """Return a local .zg* patch file, downloading it first if necessary."""
        if patch_id:
            dest_dir = DOWNLOAD_DIR / patch_id
            if dest_dir.exists():
                files = ZoomPatchBrowser._find_patch_files(dest_dir)
                if files:
                    return files[0]
        if not url:
            return None
        dest_dir = DOWNLOAD_DIR / (patch_id or "tmp_fav")
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            scraper.download_file(url, dest_dir, title=title)
            ZoomPatchBrowser._extract_archives(dest_dir)
            files = ZoomPatchBrowser._find_patch_files(dest_dir)
            return files[0] if files else None
        except Exception as exc:
            log.error("_find_or_download_fav_patch: '%s': %s", title, exc)
            return None


# ── standalone entry ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    ZoomPatchBrowser().run()
