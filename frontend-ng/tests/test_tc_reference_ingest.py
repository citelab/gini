"""Reading a book into the index.

Fetching is separated from parsing and the fetcher is injected, so everything that decides what a
section IS — where the prose lives, what the number and title are, which page comes next — is
tested against real markup with no network and no server.

The markup below is trimmed from the actual xv6 book (LaTeXML + BookML). Trimmed, not invented: a
fixture that merely resembles the real shape would test the parser perfectly and prove nothing
about whether a book can be read.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TC = Path(__file__).resolve().parents[2] / "teaching-center" / "src"
pytestmark = pytest.mark.skipif(not _TC.exists(), reason="teaching-center not checked out")
if str(_TC) not in sys.path:
    sys.path.insert(0, str(_TC))

from gini_teaching_center import references as R                # noqa: E402

BASE = "https://xv6-guide.github.io/xv6-riscv-book/"


def page(number="7.5", title="Sleep and wakeup", nxt="Ch7.S6.html", paras=("First para.",)):
    head = f"{number} {title}" if number else title
    body = "".join(f'<div id="p{i}" class="ltx_para">'
                   f'<p class="ltx_p"><span class="ltx_text">{t}</span></p></div>'
                   for i, t in enumerate(paras, 1))
    link = f'<link rel="next" href="{nxt}" title="whatever">' if nxt else ""
    return (f'<!DOCTYPE html><html><head>'
            f'<title>{head} ‣ Chapter 7 Scheduling ‣ xv6: a teaching operating system</title>'
            f'<link rel="up" href="Ch7.html">{link}</head><body>'
            f'<div class="book-summary"><nav><p class="ltx_p">Table of contents noise</p></nav></div>'
            f'<div class="ltx_page_content page-inner" id="bml-main-content">'
            f'<section class="ltx_section"><h1 class="ltx_title">{head}</h1>{body}</section>'
            f'</div></body></html>')


# ---- one page ------------------------------------------------------------------ #
def test_the_number_and_title_come_from_the_page_title():
    """The book states both in its <title>, separated from the chapter and the book by "‣" —
    a more reliable source than a heading, which carries styling spans and footnote markers."""
    p = R.parse_page(page(), BASE + "Ch7.S5.html")
    assert p["number"] == "7.5" and p["title"] == "Sleep and wakeup"


def test_the_prose_is_collected_in_order():
    p = R.parse_page(page(paras=("Scheduling and locks help.", "Sleep and wakeup provide.")))
    assert p["body"] == "Scheduling and locks help. Sleep and wakeup provide."


def test_navigation_is_not_prose():
    """The table of contents in the sidebar is marked up with the same paragraph class as the
    text. Only what is inside the main content div counts, or every section would be indexed
    carrying a copy of the whole book's contents page."""
    assert "Table of contents noise" not in R.parse_page(page())["body"]


def test_nested_divs_do_not_end_the_content_early():
    """Depth is COUNTED. A parser that stopped at the first </div> would keep one paragraph of
    every section — the content div holds a div per paragraph."""
    p = R.parse_page(page(paras=("One.", "Two.", "Three.")))
    assert p["body"] == "One. Two. Three."


def test_the_next_page_is_resolved_against_this_one():
    """`rel=next` is relative in the source; a crawl needs somewhere it can actually fetch."""
    p = R.parse_page(page(), BASE + "Ch7.S5.html")
    assert p["next_url"] == BASE + "Ch7.S6.html"


def test_a_page_with_no_next_ends_the_chain():
    assert R.parse_page(page(nxt=""), BASE)["next_url"] == ""


def test_a_chapter_contents_page_has_no_number():
    """"Chapter 7 Scheduling" carries navigation, not prose. It is walked THROUGH — `next` runs
    on to the sections beyond it — but it is not something to retrieve."""
    p = R.parse_page(page(number="", title="Chapter 7 Scheduling"))
    assert p["number"] == "" and p["title"] == "Chapter 7 Scheduling"


def test_entities_and_whitespace_come_out_readable():
    p = R.parse_page(page(paras=("Here&#x2019;s a step,\n   with  spacing.",)))
    assert p["body"] == "Here’s a step, with spacing."


def test_broken_markup_yields_a_page_rather_than_an_exception():
    """It is somebody else's HTML. A truncated download must not take the crawl down."""
    truncated = page()[: len(page()) // 2]
    assert isinstance(R.parse_page(truncated, BASE), dict)
    assert R.parse_page("", BASE)["number"] == ""


# ---- the walk ------------------------------------------------------------------- #
def _book():
    """A small book: a chapter contents page, then two sections, then the end."""
    return {
        BASE + "Ch7.html": page(number="", title="Chapter 7 Scheduling", nxt="Ch7.S5.html",
                                paras=()),
        BASE + "Ch7.S5.html": page("7.5", "Sleep and wakeup", "Ch7.S6.html",
                                   ("A process that must wait yields the CPU.",)),
        BASE + "Ch7.S6.html": page("7.6", "Code: Sleep and wakeup", "",
                                   ("The implementation uses a channel.",)),
    }


def crawl(start=BASE + "Ch7.html", pages=None, **kw):
    pages = pages if pages is not None else _book()
    return R.crawl(start, ref="xv6", fetch=lambda u: pages[u], pause=0, **kw)


def test_a_walk_collects_the_sections_in_reading_order():
    """`ord` comes from the book's own `next` chain, so a section knows where it sits without
    anyone parsing a table of contents or sorting on a dotted number as a string."""
    rows = crawl()
    assert [(r["number"], r["ord"]) for r in rows] == [("7.5", 1), ("7.6", 2)]


def test_a_contents_page_is_walked_through_but_not_indexed():
    assert all(r["number"] for r in crawl())


def test_rows_come_out_ready_for_the_store():
    r = crawl()[0]
    assert set(r) == {"id", "ref", "number", "title", "url", "body", "ord", "figures"}
    assert r["id"] == "xv6/7.5" and r["url"].endswith("Ch7.S5.html")


def test_the_figures_ride_along_without_reaching_the_sections_table():
    """`figures` is on the row for `fetch_figures` to work from, and `sections_put` writes a fixed
    column list — so the extra key is carried past it rather than into it."""
    from gini_teaching_center.store import Store
    import tempfile
    Store._instances.clear()
    st = Store(tempfile.mkdtemp())
    st.reference_put({"id": "xv6", "title": "x", "source_url": "", "licence": "",
                      "attribution": "a", "aside_titles": ""})
    st.sections_put("xv6", crawl())          # must not raise on the extra key
    assert st.reference("xv6")["sections"] == 2


def test_a_page_that_cannot_be_fetched_stops_the_walk_rather_than_skipping():
    """`next` is the only thread through the book. Past a broken link there is nothing to skip
    to, and a half-indexed book that claims to be whole is worse than a short one that says how
    far it got."""
    pages = _book()
    del pages[BASE + "Ch7.S6.html"]
    rows = crawl(pages=pages)
    assert [r["number"] for r in rows] == ["7.5"]


def test_a_loop_in_the_chain_terminates():
    """Somebody else's site. A next that points back is not a reason to fetch for ever."""
    looping = {BASE + "a": page("1.1", "A", "b", ("aaa",)),
               BASE + "b": page("1.2", "B", "a", ("bbb",))}
    rows = R.crawl(BASE + "a", ref="x", fetch=lambda u: looping[u], pause=0)
    assert [r["number"] for r in rows] == ["1.1", "1.2"]


def test_the_walk_is_bounded_even_without_a_loop():
    """A generated chain could be arbitrarily long. `max_pages` is the stop that does not depend
    on the source being well behaved."""
    endless = lambda u: page("1.1", "A", u.rsplit("/", 1)[-1] + "x", ("body",))   # noqa: E731
    assert len(R.crawl(BASE + "a", ref="x", fetch=endless, pause=0, max_pages=5)) <= 5


def test_progress_is_reported_as_it_goes():
    """Ninety fetches with a pause is a minute of silence otherwise, and an admin watching a
    command that prints nothing cannot tell it from a hang."""
    seen = []
    crawl(on_page=lambda n, t: seen.append(n))
    assert seen == ["7.5", "7.6"]


# ---- the pictures the authors drew --------------------------------------------- #
# Indexing the words and discarding the diagrams throws away the clearest half of a book whose
# figures ARE the page-table layout, the address spaces and the file-system regions.
FIG = ('<div id="bml-main-content">'
       '<p class="ltx_p">Some prose.</p>'
       '<figure class="ltx_figure"><img src="x4.png" alt="">'
       '<figcaption class="ltx_caption">Figure 3.1: <span>RISC-V addresses.</span></figcaption>'
       '</figure>'
       '<p class="ltx_p">More prose.</p></div>')


def test_a_figure_is_read_with_its_caption():
    f, = R.figures_in(FIG, BASE + "Ch3.S1.html")
    assert f["url"] == BASE + "x4.png"
    assert f["caption"] == "Figure 3.1: RISC-V addresses."


def test_reading_figures_does_not_disturb_the_prose():
    """Threading figure state through the prose parser was tried and reverted: a caption that
    never closed swallowed the rest of the section, and 3.1 Paging hardware came back as 43 words
    instead of 1072. The prose parser is the one that was already proven; figures are a small,
    flat, self-contained thing and are read separately."""
    p = R.parse_page(FIG, BASE + "Ch3.S1.html")
    assert p["body"] == "Some prose. More prose."
    assert len(p["figures"]) == 1


def test_navigation_images_are_not_figures():
    """Only what is inside the main content, and only what is marked as a figure — a book's
    chrome is full of icons."""
    noise = ('<img src="logo.png"><div id="bml-main-content">'
             '<img src="bullet.png"><p class="ltx_p">Prose.</p></div>')
    assert R.figures_in(noise, BASE) == []


def test_a_figure_with_no_caption_is_still_a_figure():
    m = '<div id="bml-main-content"><figure class="ltx_figure"><img src="x9.png"></figure></div>'
    f, = R.figures_in(m, BASE)
    assert f["url"].endswith("x9.png") and f["caption"] == ""


def test_the_same_picture_twice_is_carried_once():
    m = ('<div id="bml-main-content">'
         '<figure class="ltx_figure"><img src="x4.png"></figure>'
         '<figure class="ltx_figure"><img src="x4.png"></figure></div>')
    assert len(R.figures_in(m, BASE)) == 1


def test_a_section_with_no_figures_reports_none():
    assert R.parse_page(page(), BASE)["figures"] == []


def test_broken_markup_yields_no_figures_rather_than_an_exception():
    for junk in ("", "<figure class='ltx_figure'", "<div id='bml-main-content'><figure"):
        assert isinstance(R.figures_in(junk, BASE), list)
