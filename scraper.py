#!/usr/bin/env python3
"""
Tonelib Forum Scraper – Zoom G3n / G3Xn / G5n patches
Crawls https://tonelib.net/forums/forums/zoom-g3n-g3xn-g5n.15/
and saves structured JSON files to ./index/
"""
import getpass
import json
import re
import time
import zipfile
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from rich.console import Console
from rich.markup import escape as markup_escape
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

# ── configuration ───────────────────────────────────────────────────────────
BASE_URL   = "https://tonelib.net/forums"
FORUM_PATH = "/forums/zoom-g3n-g3xn-g5n.15/"
FORUM_URL  = BASE_URL + FORUM_PATH

BASE_DIR          = Path(__file__).parent
INDEX_DIR         = BASE_DIR / "index"
DEBUG_DIR         = BASE_DIR / "debug"

CONFIG_DIR        = Path.home() / ".config" / "zoomdownloader"
COOKIES_FILE      = CONFIG_DIR / "cookies.json"
CREDENTIALS_FILE  = CONFIG_DIR / "credentials.json"

REQUEST_DELAY = 0.6   # seconds between requests
TIMEOUT       = 20    # seconds

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": BASE_URL + "/",
}

console = Console()


# ── helpers ──────────────────────────────────────────────────────────────────

def _thread_id_from_url(url: str) -> str:
    """Extract numeric XenForo thread id from a thread URL."""
    m = re.search(r"\.(\d+)/?(?:[#?]|$)", url)
    return m.group(1) if m else str(abs(hash(url)) % 10**9)


def _extract_field(patterns: list[str], text: str) -> str:
    """Try each regex pattern in turn; return first match or empty string."""
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            return m.group(1).strip()
    return ""


# ── main class ───────────────────────────────────────────────────────────────

class ForumScraper:
    def __init__(self, debug: bool = False):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.debug = debug
        INDEX_DIR.mkdir(exist_ok=True)
        if debug:
            DEBUG_DIR.mkdir(exist_ok=True)
            console.print(f"[dim]Debug mode ON – HTML dumps → {DEBUG_DIR}[/dim]")

    # ── debug helpers ────────────────────────────────────────────────────────

    def _dbg(self, msg: str) -> None:
        if self.debug:
            console.print(f"[dim cyan][DBG][/dim cyan] {markup_escape(msg)}")

    def _dbg_save(self, name: str, content: str) -> None:
        """Save raw HTML to debug/ so it can be inspected offline."""
        if not self.debug:
            return
        path = DEBUG_DIR / name
        path.write_text(content, encoding="utf-8", errors="replace")
        console.print(f"[dim cyan][DBG][/dim cyan] saved → [underline]{path}[/underline]")

    # ── session persistence ──────────────────────────────────────────────────

    def save_cookies(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        simple = {c.name: c.value for c in self.session.cookies}
        COOKIES_FILE.write_text(json.dumps(simple, indent=2))

    def save_credentials(self, username: str, password: str) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CREDENTIALS_FILE.write_text(
            json.dumps({"username": username, "password": password}, indent=2)
        )
        # Restrict file to owner-read/write only (mode 600)
        CREDENTIALS_FILE.chmod(0o600)

    def load_credentials(self) -> tuple[str, str] | None:
        if not CREDENTIALS_FILE.exists():
            return None
        try:
            data = json.loads(CREDENTIALS_FILE.read_text())
            u, p = data.get("username", ""), data.get("password", "")
            return (u, p) if u and p else None
        except Exception:
            return None

    def load_cookies(self) -> bool:
        if not COOKIES_FILE.exists():
            return False
        try:
            cookies = json.loads(COOKIES_FILE.read_text())
            for name, value in cookies.items():
                self.session.cookies.set(name, value, domain="tonelib.net", path="/")
            return True
        except Exception:
            return False

    # ── auth ─────────────────────────────────────────────────────────────────

    def is_logged_in(self) -> bool:
        try:
            resp = self.session.get(FORUM_URL, timeout=TIMEOUT, allow_redirects=True)
            self._dbg(f"is_logged_in: final URL = {resp.url}  status={resp.status_code}")
            if "login" in resp.url.lower():
                self._dbg("is_logged_in: redirected to login page → not authenticated")
                self._dbg_save("is_logged_in_redirect.html", resp.text)
                return False
            soup = BeautifulSoup(resp.text, "html.parser")
            # XenForo 1 theme used by tonelib.net
            member_nav  = soup.select_one("ul.visitorTabs")
            account_lnk = soup.select_one("a.accountPopup")
            logout_lnk  = soup.select_one("a.LogOut")
            self._dbg(
                f"is_logged_in selectors: "
                f"ul.visitorTabs={'found' if member_nav else 'missing'}  "
                f"a.accountPopup={'found' if account_lnk else 'missing'}  "
                f"a.LogOut={'found' if logout_lnk else 'missing'}"
            )
            result = bool(member_nav or account_lnk or logout_lnk)
            if not result:
                self._dbg_save("is_logged_in_fail.html", resp.text)
            return result
        except Exception as exc:
            self._dbg(f"is_logged_in: exception – {exc}")
            return False

    def login(self, username: str, password: str) -> tuple[bool, str]:
        try:
            # ── Step 1: fetch login page and grab CSRF token ─────────────────
            login_page_url = f"{BASE_URL}/login/"
            self._dbg(f"GET {login_page_url}")
            resp = self.session.get(login_page_url, timeout=TIMEOUT)
            self._dbg(f"login page: status={resp.status_code}  final_url={resp.url}")
            self._dbg_save("login_page.html", resp.text)

            soup = BeautifulSoup(resp.text, "html.parser")
            token_inp = soup.find("input", {"name": "_xfToken"})
            xf_token = token_inp["value"] if token_inp else ""
            self._dbg(f"_xfToken: {'found ('+repr(xf_token[:8])+'…)' if xf_token else 'NOT FOUND – POST will likely fail'}")

            # Also log all form fields present so we can spot unexpected structure
            if self.debug:
                form = soup.find("form")
                if form:
                    field_names = [i.get("name") for i in form.find_all("input") if i.get("name")]
                    self._dbg(f"form fields: {field_names}")
                else:
                    self._dbg("no <form> found on login page!")

            # ── Step 2: POST credentials ──────────────────────────────────────
            post_url = f"{BASE_URL}/login/login"
            self._dbg(f"POST {post_url}  user={username!r}")
            resp = self.session.post(
                post_url,
                data={
                    "login": username,
                    "password": password,
                    "_xfToken": xf_token,
                    "remember": "1",
                },
                timeout=TIMEOUT,
                allow_redirects=True,
            )
            self._dbg(f"POST response: status={resp.status_code}  final_url={resp.url}")
            self._dbg(f"cookies after POST: {dict(self.session.cookies)}")
            self._dbg_save("login_post_response.html", resp.text)

            # ── Step 3: check for inline error message ────────────────────────
            soup = BeautifulSoup(resp.text, "html.parser")
            err = soup.select_one(".blockMessage--error, .error, .js-errorOverlay")
            if err:
                self._dbg(f"inline error element found: {err.get_text(strip=True)!r}")
                return False, err.get_text(strip=True)

            # ── Step 4: confirm we're actually authenticated ──────────────────
            self._dbg("POST succeeded without inline error – verifying session…")
            if self.is_logged_in():
                self.save_cookies()
                return True, "Login successful."

            return False, "Login failed – wrong credentials or unexpected page."

        except requests.RequestException as exc:
            self._dbg(f"requests exception: {exc}")
            return False, f"Network error during login: {exc}"

    def ensure_auth(self) -> bool:
        """Load saved session or prompt for credentials. Returns True if authenticated."""
        if self.load_cookies():
            console.print("[dim]Checking saved session…[/dim]")
            if self.is_logged_in():
                console.print("[green]✓[/green] Using saved session.")
                return True
            console.print("[yellow]Session expired – re-authenticating…[/yellow]")

        # Try saved credentials before prompting
        saved = self.load_credentials()
        if saved:
            username, password = saved
            console.print(f"[dim]Using saved credentials for {username}[/dim]")
        else:
            console.print(f"[bold]Login required for[/bold] {FORUM_URL}")
            username = input("Username: ").strip()
            password = getpass.getpass("Password: ")
            self.save_credentials(username, password)

        ok, msg = self.login(username, password)
        if ok:
            console.print(f"[green]✓[/green] {msg}")
        else:
            console.print(f"[red]✗[/red] {msg}")
        return ok

    # ── crawling ─────────────────────────────────────────────────────────────

    def _get_page_threads(self, page_url: str) -> tuple[list[dict], str | None]:
        """
        Fetch one forum listing page.
        Returns (list of {url, title} dicts, next_page_url or None).
        """
        resp = self.session.get(page_url, timeout=TIMEOUT)
        if "login" in resp.url.lower():
            console.print("[red]Redirected to login – session lost.[/red]")
            return [], None

        soup = BeautifulSoup(resp.text, "html.parser")

        threads = []
        for item in soup.select("li.discussionListItem"):
            link = item.select_one("a.PreviewTooltip")
            if link:
                threads.append({
                    "url": urljoin(BASE_URL + "/", link["href"]),
                    "title": link.get_text(strip=True),
                })

        nav_div = soup.select_one(".PageNav")
        current_page = int(nav_div["data-page"]) if nav_div else 0
        last_page    = int(nav_div["data-last"])  if nav_div else 0
        if nav_div and current_page < last_page:
            base_pattern = nav_div.get("data-baseurl", "")
            next_href    = base_pattern.replace("{{sentinel}}", str(current_page + 1))
            next_url     = urljoin(BASE_URL + "/", next_href)
        else:
            next_url = None

        return threads, next_url

    # ── thread parsing ───────────────────────────────────────────────────────

    def parse_thread(self, url: str) -> dict | None:
        try:
            resp = self.session.get(url, timeout=TIMEOUT)
        except requests.RequestException as exc:
            console.print(f"  [red]Request error: {exc}[/red]")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # Thread title – XenForo 1: <h1> inside .titleBar
        title_el = soup.select_one(".titleBar h1, h1")
        title = title_el.get_text(strip=True) if title_el else ""

        # First post – XenForo 1: first <li class="message"> in ol.messageList
        first_post = soup.select_one("li.message")
        if not first_post:
            return None

        # Post body text – XenForo 1: .messageContent or .messageText
        body = first_post.select_one(".messageContent, .messageText")
        if not body:
            return None

        raw_text = body.get_text("\n", strip=True)

        # ── structured field extraction ──────────────────────────────────────
        device = _extract_field(
            [
                r"^device\s*[:\-]\s*(.+)$",
                r"device\s*[:\-]\s*(.+?)(?:\n|$)",
            ],
            raw_text,
        )
        firmware = _extract_field(
            [
                r"^firmware(?:\s+ver(?:sion)?)?\s*[:\-]\s*(.+)$",
                r"firmware(?:\s+ver(?:sion)?)?\s*[:\-]\s*(.+?)(?:\n|$)",
            ],
            raw_text,
        )
        name_on_device = _extract_field(
            [
                r"^name(?:\s+on\s+device)?\s*[:\-]\s*(.+)$",
                r"name(?:\s+on\s+device)?\s*[:\-]\s*(.+?)(?:\n|$)",
            ],
            raw_text,
        )
        optimized_for = _extract_field(
            [
                r"^optimized\s+for\s*[:\-]\s*(.+)$",
                r"optimized\s+for\s*[:\-]\s*(.+?)(?:\n|$)",
                r"^(?:guitar|bass|style|amp|genre)\s*[:\-]\s*(.+)$",
            ],
            raw_text,
        )
        patch_comments = _extract_field(
            [
                r"(?:comments?|notes?|description)\s*[:\-]\s*([\s\S]+)",
            ],
            raw_text,
        )
        if not patch_comments:
            patch_comments = raw_text  # Fall back to full text

        # ── download URL extraction ──────────────────────────────────────────
        # XenForo 1 stores attachments in .attachedFiles, with links like
        # "attachments/<slug>.<id>/"  — the filename is in h6.filename
        download_urls: list[str] = []

        # 1. Attachment filenames in .attachedFiles (most reliable)
        for li in soup.select(".attachedFiles li.attachment"):
            fname_el = li.select_one("h6.filename a[href], a.filename[href]")
            if not fname_el:
                # Fall back: any link inside the attachment li
                fname_el = li.select_one("a[href]")
            if fname_el:
                fname = fname_el.get_text(strip=True)
                href  = fname_el["href"]
                # Include only if filename looks like a patch or we don't know
                if not fname or re.search(r"\.zg", fname, re.IGNORECASE) or "attachments/" in href:
                    full = urljoin(BASE_URL + "/", href)
                    if full not in download_urls:
                        download_urls.append(full)

        # 2. Any link in post body whose text or href contains a .zg* filename
        for a in first_post.select("a[href]"):
            href = a["href"]
            txt  = a.get_text(strip=True)
            if re.search(r"\.zg[0-9a-z]", href + " " + txt, re.IGNORECASE):
                full = urljoin(BASE_URL + "/", href)
                if full not in download_urls:
                    download_urls.append(full)

        return {
            "title": title,
            "device": device,
            "firmware": firmware,
            "name_on_device": name_on_device,
            "optimized_for": optimized_for,
            "patch_comments": patch_comments,
            "forum_url": url,
            "download_urls": download_urls,
        }

    # ── public entry-point ───────────────────────────────────────────────────

    def scrape(self, force: bool = False) -> None:
        if not self.ensure_auth():
            console.print("[red]Cannot proceed without authentication.[/red]")
            return

        page_url: str | None = FORUM_URL
        page_num = 0
        total_indexed = 0
        total_skipped = 0

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}"),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as prog:
            page_task   = prog.add_task("Page 1", total=None)
            thread_task = prog.add_task("Threads", total=None, visible=False)

            while page_url:
                page_num += 1
                prog.update(page_task, description=f"[bold cyan]Page {page_num}")

                try:
                    threads, next_url = self._get_page_threads(page_url)
                except requests.RequestException as exc:
                    console.print(f"[red]Network error on page {page_num}: {exc}[/red]")
                    break

                if not threads:
                    break

                prog.update(thread_task, total=len(threads), completed=0, visible=True)

                for i, thread in enumerate(threads, 1):
                    tid      = _thread_id_from_url(thread["url"])
                    out_path = INDEX_DIR / f"{tid}.json"

                    prog.update(
                        thread_task,
                        completed=i,
                        description=f"[dim]{thread['title'][:50]}",
                    )

                    if out_path.exists() and not force:
                        total_skipped += 1
                        continue

                    time.sleep(REQUEST_DELAY)
                    data = self.parse_thread(thread["url"])
                    if data and data.get("download_urls"):
                        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
                        total_indexed += 1

                page_url = next_url
                if page_url:
                    time.sleep(REQUEST_DELAY)

        console.print(
            f"\n[bold green]Done![/bold green] "
            f"Indexed [cyan]{total_indexed}[/cyan] new  •  "
            f"skipped [dim]{total_skipped}[/dim] already indexed  •  "
            f"JSON files in [cyan]{INDEX_DIR.relative_to(BASE_DIR)}[/cyan]"
        )

    # ── file download (used by viewer) ───────────────────────────────────────

    def download_file(self, url: str, dest_dir: Path, title: str = "") -> Path:
        """
        Download *url* into *dest_dir* using the current session.
        If the downloaded file is a ZIP, it is extracted into a subfolder
        named after *title* (or the zip stem if no title given), and the
        zip file is removed.  Returns the saved Path (file or folder).
        Raises on failure.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)

        resp = self.session.get(url, timeout=TIMEOUT, stream=True, allow_redirects=True)
        resp.raise_for_status()

        # Derive filename
        filename = url.rstrip("/").split("/")[-1]
        cd = resp.headers.get("Content-Disposition", "")
        m = re.search(r'filename[^;=\n]*=\s*["\']?([^"\';\n]+)', cd, re.IGNORECASE)
        if m:
            filename = m.group(1).strip().strip('"\'')

        if not filename:
            filename = "patch"

        # Sanitise
        filename = re.sub(r'[\\/:*?"<>|]', "_", filename)
        dest = dest_dir / filename

        with dest.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    fh.write(chunk)

        if zipfile.is_zipfile(dest):
            folder_name = re.sub(r'[\\/:*?"<>|]', "_", title.strip()) if title.strip() else dest.stem
            extract_dir = dest_dir / folder_name
            extract_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(dest) as zf:
                zf.extractall(extract_dir)
            dest.unlink()
            return extract_dir

        return dest
