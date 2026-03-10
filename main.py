#!/usr/bin/env python3
"""
zoomdownloader – Tonelib forum patch scraper & browser
for Zoom G3n / G3Xn / G5n guitar processors.

Usage
-----
  python main.py scrape              # crawl & index forum posts
  python main.py scrape --force      # re-index already-seen threads
  python main.py browse              # open the TUI browser
  python main.py upload FILE SLOT    # upload patch to pedal slot
  python main.py                     # → browse (default)
"""
import argparse
import sys


def cmd_scrape(args: argparse.Namespace) -> None:
    from scraper import ForumScraper

    scraper = ForumScraper(debug=args.debug)
    scraper.scrape(force=args.force)


def cmd_browse(_args: argparse.Namespace) -> None:
    from viewer import ZoomPatchBrowser

    ZoomPatchBrowser().run()


def cmd_upload(args: argparse.Namespace) -> None:
    from zoom_midi import ZoomDevice, parse_patch_file
    from rich.console import Console

    console = Console()
    patch_file = args.file
    slot = args.slot

    console.print(f"Uploading [cyan]{patch_file}[/] to slot [cyan]{slot}[/]…")

    try:
        with ZoomDevice() as dev:
            name = dev.upload_patch(patch_file, slot)
            console.print(
                f"[green]✓[/green] Patch [bold]{name!r}[/bold] uploaded to slot {slot}."
            )
    except Exception as exc:
        console.print(f"[red]✗[/red] Upload failed: {exc}")
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zoomdownloader",
        description="Scrape & browse Tonelib G3n/G3Xn/G5n patches.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command")

    # scrape
    sp = sub.add_parser("scrape", help="Crawl Tonelib forum and save patches to index/")
    sp.add_argument(
        "--force",
        action="store_true",
        help="Re-scrape threads that are already in the index.",
    )
    sp.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging and save HTML responses to debug/.",
    )
    sp.set_defaults(func=cmd_scrape)

    # browse
    bp = sub.add_parser("browse", help="Open TUI browser for indexed patches")
    bp.set_defaults(func=cmd_browse)

    # upload
    up = sub.add_parser(
        "upload",
        help="Upload a .zg3xn/.zg3n/.zg5n patch file to a pedal memory slot",
    )
    up.add_argument("file", help="Path to the patch file (.zg3xn, .zg3n, .zg5n)")
    up.add_argument("slot", type=int, help="Target memory slot (0–199)")
    up.set_defaults(func=cmd_upload)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not hasattr(args, "func"):
        # No sub-command → default to browse
        args.func = cmd_browse

    args.func(args)


if __name__ == "__main__":
    main()
