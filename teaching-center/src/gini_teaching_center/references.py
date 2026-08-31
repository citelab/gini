"""Reading a book into the index.

Fetching is separated from PARSING throughout, and the fetcher is injected. Everything that decides
what a section is — where the prose lives, what the number and title are, which page comes next —
is a pure function over a string, so it is tested against real saved markup with no network, no
server and no book.

**Stdlib only.** The Teaching Center depends on `gini-core` and nothing else, and adding an HTML
library to a server a department installs on a VM is a bigger cost than a hundred lines of parser.

**Not fetched at question time, ever.** Indexing is an administrative act: it reaches out to a third
party some ninety times, and a tutor that did that while a student waited would be slow, fragile,
and rude to somebody else's server. The result lives in the database.

The shape it reads is LaTeXML + BookML, which is what the xv6 book is built with — prose in
`<p class="ltx_p">` inside `#bml-main-content`, and, usefully, a `<link rel="next">` on every page.
That last one means the book states its own reading order, so the crawl follows the text rather
than guessing at a table of contents, and section ordering comes out right for free.
"""
from __future__ import annotations

import html as _html
import re
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser

#: Where the prose lives, and what wraps it. Named here rather than inline so a second book shape
#: is a change in one place.
_MAIN_ID = "bml-main-content"
_PARA_CLASS = "ltx_p"
_FIG_CLASS = "ltx_figure"
_CAP_CLASS = "ltx_caption"

#: The book's own separator between "7.5 Sleep and wakeup", its chapter, and the book title.
_TITLE_SEP = "‣"

#: A section number as the title states it: 7.5, or 7.5.1. A page whose title carries no number is
#: a chapter's table of contents, a bibliography or an index — navigation, not prose.
_NUMBER_RE = re.compile(r"^(\d+(?:\.\d+)+)\s+(.*)$")

TIMEOUT = 20.0
#: A crawl walks a stranger's server. One page at a time, with a pause, because we are a guest.
POLITE_PAUSE = 0.25
MAX_PAGES = 500


class _Page(HTMLParser):
    """One page reduced to what the index needs: its title, its prose, and where to go next."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.next_href = ""
        self.paras: list[str] = []
        self._in_title = False
        self._depth = 0          # >0 once inside the main content div
        self._para: list[str] | None = None

    def handle_starttag(self, tag, attrs) -> None:
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "link" and (a.get("rel") or "").lower() == "next" and a.get("href"):
            self.next_href = a["href"]
        elif self._depth:
            # Nesting is counted, not searched for: the main content holds divs of its own, and a
            # parser that stopped at the first </div> would keep one paragraph of every section.
            if tag == "div":
                self._depth += 1
            if tag == "p" and _PARA_CLASS in (a.get("class") or "").split():
                self._para = []
        elif tag == "div" and a.get("id") == _MAIN_ID:
            self._depth = 1

    def handle_endtag(self, tag) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "p" and self._para is not None:
            text = _tidy("".join(self._para))
            if text:
                self.paras.append(text)
            self._para = None
        elif tag == "div" and self._depth:
            self._depth -= 1

    def handle_data(self, data) -> None:
        if self._in_title:
            self.title += data
        elif self._para is not None:
            self._para.append(data)


#: A figure block, and the picture and caption inside it. Matched with a regex rather than woven
#: into the parser above, and that is a decision made the hard way: threading figure state through
#: the prose parser meant a caption that never closed swallowed the rest of the section, and 3.1
#: Paging hardware came back as 43 words. A figure is a small, flat, self-contained thing — the
#: prose is the part that needs a real parser, and it keeps the one that was already proven.
_FIGURE_RE = re.compile(r'<(figure|div)[^>]*class="[^"]*ltx_figure[^"]*"[^>]*>(.*?)</\1>',
                        re.S | re.I)
_IMG_RE = re.compile(r'<img[^>]*src="([^"]+)"', re.I)
_CAPTION_RE = re.compile(r'<(figcaption|p)[^>]*class="[^"]*ltx_caption[^"]*"[^>]*>(.*?)</\1>',
                         re.S | re.I)
_TAGS_RE = re.compile(r'<[^>]+>')


def figures_in(markup: str, url: str = "") -> list[dict]:
    """The pictures the authors drew for this passage, with their captions.

    Kept because for a book whose diagrams ARE the page-table layout, the address spaces and the
    file-system regions, indexing the words and discarding the pictures throws away the clearest
    half. Downloaded once at index time and served from the course server, so nobody fetches from a
    stranger's site while a student waits.
    """
    out, seen = [], set()
    body = markup.split(f'id="{_MAIN_ID}"', 1)[-1] if _MAIN_ID in markup else markup
    for _tag, block in _FIGURE_RE.findall(body):
        m = _IMG_RE.search(block)
        if not m:
            continue
        src = urllib.parse.urljoin(url, _html.unescape(m.group(1)))
        if src in seen:
            continue
        seen.add(src)
        cap = _CAPTION_RE.search(block)
        out.append({"url": src,
                    "caption": _tidy(_TAGS_RE.sub(" ", cap.group(2))) if cap else ""})
    return out


def _tidy(text: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(text or "")).strip()


def parse_page(markup: str, url: str = "") -> dict:
    """One page -> {number, title, body, next_url}. Never raises on bad markup.

    `number` is empty for a page that is not a numbered section — a chapter's contents page, the
    bibliography, the index. Those carry navigation rather than prose and are not worth retrieving;
    the crawl still follows them, because `next` runs through them to the sections beyond.
    """
    p = _Page()
    try:
        p.feed(markup or "")
    except Exception:                          # noqa: BLE001 — a partial parse still has pages
        pass
    head = _tidy(p.title).split(_TITLE_SEP)[0].strip()
    m = _NUMBER_RE.match(head)
    return {"number": m.group(1) if m else "",
            "title": (m.group(2) if m else head).strip(),
            "body": " ".join(p.paras).strip(),
            "figures": figures_in(markup or "", url),
            "next_url": urllib.parse.urljoin(url, p.next_href) if p.next_href else ""}


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "gini-teaching-center/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
    return raw.decode("utf-8", "replace")


def crawl(start_url: str, *, ref: str, fetch=None, max_pages: int = MAX_PAGES,
          on_page=None, pause: float = POLITE_PAUSE) -> list[dict]:
    """Walk a book from `start_url` along its own `rel=next` chain, collecting numbered sections.

    Returns rows ready for `Store.sections_put`. A page that cannot be fetched ends the walk rather
    than skipping ahead: `next` is the only thread through the book, so past a broken link there is
    nothing to skip to, and a half-indexed book that claims to be whole is worse than a short one
    that says how far it got.
    """
    fetch = fetch or _get
    rows: list[dict] = []
    seen: set[str] = set()
    url, order = start_url, 0
    while url and url not in seen and len(seen) < max_pages:
        seen.add(url)
        try:
            page = parse_page(fetch(url), url)
        except Exception:                      # noqa: BLE001 — the walk stops where the book does
            break
        if page["number"] and page["body"]:
            order += 1
            rows.append({"id": f"{ref}/{page['number']}", "ref": ref,
                         "number": page["number"], "title": page["title"],
                         "url": url, "body": page["body"], "ord": order})
            if on_page:
                on_page(page["number"], page["title"])
        url = page["next_url"]
        if url and pause:
            time.sleep(pause)
    return rows


__all__ = ["crawl", "parse_page", "MAX_PAGES", "TIMEOUT"]
