"""
Browser regression tests for the /docs reference UI.

    PYTHONPATH=. pytest tests/e2e/test_docs_reference.py -q --browser chromium

or, from the ui/ directory, `npm run test:e2e`.

Prerequisites
    pip install -r requirements-dev.txt     # pytest-playwright, playwright
    playwright install chromium             # the browser binary
    node + npm                              # to build and serve the Next app

The module builds `ui/` and serves the production build on a free port for the
duration of the session, because these are regressions in behaviour that only
exists after hydration and some of them differ between a development and a
production build. Point DOCS_BASE_URL at an already running server to skip the
build, which is what you want while iterating:

    DOCS_BASE_URL=http://127.0.0.1:3000 PYTHONPATH=. pytest tests/e2e/test_docs_reference.py

Everything here skips rather than fails when node or the browser binary is
missing, the way tests/sql skips without SQLTEST_DSN. CI does not run this file:
.github/workflows/tests.yml excludes tests/e2e because it needs a real browser
and a running server.

Several tests deliberately use a FRESH browser context. The reference loads
Instrument Sans with `display: swap`, so a reader arriving with a cold font cache
measures code-block overflow against fallback metrics. Reusing one warm context
hides that entirely, and did: an earlier hand-run suite reported this behaviour
as correct because by then the fonts were cached.
"""
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[2] / "ui"
PAGES = ["", "quickstart", "authentication", "create-a-project", "push-items",
         "poll-results", "compare-versions", "failure-library", "webhooks",
         "task-config", "errors"]

# ── Prerequisites ───────────────────────────────────────────────────────────
#
# These fail closed. They used to skip, and a run in which every test skips
# exits 0, so `npm run test:e2e` reported success having executed no browser
# test at all. On a clean checkout, where ui/node_modules does not exist yet,
# that is exactly what happened. A regression suite that goes green without
# running is worse than no suite, because it is believed.
#
# DOCS_E2E_OPTIONAL=1 restores skipping, for a contributor with no browser stack
# who is working on something else.

OPTIONAL = os.environ.get("DOCS_E2E_OPTIONAL") == "1"
_HINT = "Set DOCS_E2E_OPTIONAL=1 to skip these tests instead of failing."


def _unmet(reason: str):
    """A prerequisite is missing. Fail, unless skipping was explicitly asked for."""
    if OPTIONAL:
        pytest.skip(reason)
    pytest.fail(f"{reason}\n{_HINT}", pytrace=False)


@pytest.fixture(scope="session", autouse=True)
def _chromium_is_installed():
    """Both browser prerequisites, checked once up front.

    Imported here rather than at module scope so every prerequisite reports the
    same way: a module-level skip collects nothing, and pytest then exits 5 for
    an empty run, where the other two exit 0. Launched once so a missing binary
    is one clear sentence rather than the same launch error repeated by
    forty-six tests."""
    try:
        from playwright.sync_api import Error as PlaywrightError, sync_playwright
    except ImportError:
        _unmet("playwright is not installed: pip install -r requirements-dev.txt")
        return
    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
    except PlaywrightError as exc:
        first = str(exc).strip().splitlines()[0]
        _unmet("chromium is not installed: run `playwright install chromium`\n"
               f"playwright said: {first}")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def base_url(_chromium_is_installed) -> str:
    """The reference, served. An existing server via DOCS_BASE_URL wins.

    Depends on the browser check so the cheap prerequisite is reported before
    this spends two minutes building a site nothing can then open."""
    existing = os.environ.get("DOCS_BASE_URL")
    if existing:
        # yield, not return: this is a generator fixture, and returning from one
        # ends it without producing a value.
        yield existing.rstrip("/")
        return

    npm = shutil.which("npm")
    if not npm:
        _unmet("node/npm is not on PATH, so the reference cannot be built")
    if not (UI / "node_modules").is_dir():
        _unmet(f"{UI}/node_modules is missing: run `npm ci` in ui/ first")

    build = subprocess.run([npm, "run", "build"], cwd=UI,
                           capture_output=True, text=True)
    if build.returncode != 0:
        pytest.fail("next build failed:\n" + build.stdout[-3000:] + build.stderr[-3000:])

    port = _free_port()
    server = subprocess.Popen([npm, "run", "start", "--", "-p", str(port)], cwd=UI,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            if server.poll() is not None:
                pytest.fail("the server exited before it was ready")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.5)
        else:
            pytest.fail(f"the server was not listening on {port} within 60s")
        yield url
    finally:
        server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()


@pytest.fixture
def cold(browser, base_url):
    """A context with nothing cached, which is how a first-time reader arrives."""
    made = []

    def _open(width=1440, height=1000, **kw):
        ctx = browser.new_context(viewport={"width": width, "height": height}, **kw)
        made.append(ctx)
        return ctx.new_page()

    yield _open
    for ctx in made:
        ctx.close()


# ── Shared probes, kept as strings so a failure prints the measurement ───────

PAGE_STATE = """() => {
  const heads = [...document.querySelectorAll('.docs-main h2, .docs-main h3')];
  const toc = [...document.querySelectorAll('.docs-toc a')];
  return {
    heads: heads.length,
    withId: heads.filter(h => h.id).length,
    pres: document.querySelectorAll('.docs-pre').length,
    strips: document.querySelectorAll('.docs-pre .pre-head').length,
    toc: toc.length,
    tocTitles: document.querySelectorAll('.docs-toc .docs-toc-t').length,
    tocDead: toc.filter(a => !document.getElementById(a.getAttribute('href').slice(1))).length,
    tocText: toc.map(a => a.textContent),
  };
}"""

SCROLLABLE = """() => {
  const c = [...document.querySelectorAll('.docs-pre code')];
  return {
    total: c.length,
    scrolls: c.filter(e => e.scrollWidth > e.clientWidth).length,
    tabbable: c.filter(e => e.hasAttribute('tabindex')).length,
    named: c.filter(e => e.getAttribute('role') === 'region' && e.getAttribute('aria-label')).length,
    mismatch: c.filter(e => (e.scrollWidth > e.clientWidth) !== e.hasAttribute('tabindex')).length,
    labels: c.filter(e => e.hasAttribute('aria-label')).map(e => e.getAttribute('aria-label')),
  };
}"""

# Composite contrast: the colour's own alpha, every ancestor's opacity, over the
# nearest opaque background. A ratio read off the stylesheet misses both.
CONTRAST = """(sel) => {
  const lum = c => { const s = c.map(v => { v /= 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126*s[0] + 0.7152*s[1] + 0.0722*s[2]; };
  const P = s => (s.match(/[\\d.]+/g) || [0,0,0]).slice(0,3).map(Number);
  const A = s => { const m = s.match(/[\\d.]+/g); return m && m.length > 3 ? Number(m[3]) : 1; };
  const el = document.querySelector(sel);
  if (!el) return null;
  let n = el, bg = [0,0,0];
  while (n && n !== document.documentElement) {
    const c = getComputedStyle(n).backgroundColor;
    if (c && A(c) > 0.99) { bg = P(c); break; }
    n = n.parentElement;
  }
  const cs = getComputedStyle(el);
  let op = 1, q = el;
  while (q && q !== document.documentElement) { op *= parseFloat(getComputedStyle(q).opacity); q = q.parentElement; }
  const a = A(cs.color) * op;
  const fg = P(cs.color).map((v, i) => v*a + bg[i]*(1-a));
  const [x, y] = [lum(fg), lum(bg)].sort((p, q) => q - p);
  return { ratio: +((x + 0.05) / (y + 0.05)).toFixed(2), size: parseFloat(cs.fontSize) };
}"""


def settle(page):
    """Wait for hydration, for the font swap that follows it, and for the work
    the swap schedules.

    Two frames rather than a sleep: the component re-measures overflow in a
    `document.fonts.ready` handler, and the resize path coalesces into a
    requestAnimationFrame. Waiting for fonts and then two frames means both have
    run, deterministically, instead of hoping a fixed delay covered them. This
    waits on font and frame state, never on the attributes a test asserts."""
    page.wait_for_load_state("networkidle")
    page.evaluate("""() => document.fonts.ready.then(
        () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))))""")


# ── Finding 1: a malformed fragment must not take the page down ──────────────

@pytest.mark.parametrize("fragment", ["#%", "#%zz", "#a%", "#%E0%A4%A", "#100%"])
def test_a_malformed_fragment_leaves_the_reference_working(cold, base_url, fragment):
    """decodeURIComponent throws URIError on these. Unguarded it reached the
    error boundary and replaced the whole route with 'This page couldn't load',
    sidebar included, so the reader could not even navigate away."""
    page = cold()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e).split("\n")[0]))
    page.goto(f"{base_url}/docs/push-items{fragment}")
    settle(page)

    state = page.evaluate(PAGE_STATE)
    assert not errors, f"{fragment} raised {errors}"
    assert state["pres"] == 5, f"{fragment} lost the code blocks: {state}"
    assert state["strips"] == state["pres"], f"{fragment}: {state}"
    assert state["withId"] == state["heads"] == 3, f"{fragment}: {state}"
    assert state["toc"] == 3 and state["tocDead"] == 0, f"{fragment}: {state}"
    # the sidebar is still there to navigate away by
    assert page.locator(".docs-nav a:has-text('Webhooks')").is_visible()
    # and search is still wired
    page.keyboard.press("Meta+k" if page.evaluate("() => /Mac/.test(navigator.platform)") else "Control+k")
    page.wait_for_timeout(400)
    assert page.locator("#docs-search").is_visible(), f"{fragment} left search dead"


def test_a_malformed_fragment_scrolls_nowhere(cold, base_url):
    page = cold()
    page.goto(f"{base_url}/docs/push-items#%zz")
    settle(page)
    assert page.evaluate("() => Math.round(window.scrollY)") == 0


def test_a_valid_encoded_fragment_lands_clear_of_the_sticky_bar(cold, base_url):
    """Percent-encoded, so it exercises the decode rather than skipping it."""
    page = cold()
    page.goto(f"{base_url}/docs/push-items#%72esponse")   # 'r' encoded
    settle(page)
    d = page.evaluate("""() => {
      const t = document.getElementById('response');
      const b = document.querySelector('.docs-topbar').getBoundingClientRect();
      return { y: Math.round(window.scrollY), top: Math.round(t.getBoundingClientRect().top),
               bar: Math.round(b.bottom) };
    }""")
    assert d["y"] > 0, f"an encoded fragment did not scroll: {d}"
    assert d["top"] >= d["bar"], f"the heading landed under the sticky bar: {d}"


def test_a_plain_fragment_lands_clear_of_the_sticky_bar(cold, base_url):
    page = cold()
    page.goto(f"{base_url}/docs/push-items#response")
    settle(page)
    d = page.evaluate("""() => {
      const t = document.getElementById('response');
      const b = document.querySelector('.docs-topbar').getBoundingClientRect();
      return { y: Math.round(window.scrollY), top: Math.round(t.getBoundingClientRect().top),
               bar: Math.round(b.bottom) };
    }""")
    assert d["y"] > 0 and d["top"] >= d["bar"], d


def test_a_fragment_naming_nothing_is_harmless(cold, base_url):
    page = cold()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base_url}/docs/push-items#does-not-exist")
    settle(page)
    assert not errors
    assert page.evaluate("() => Math.round(window.scrollY)") == 0
    assert page.evaluate(PAGE_STATE)["tocDead"] == 0


def test_a_restored_scroll_position_is_not_overridden(cold, base_url):
    """The retry is guarded on scrollY === 0 so a reload that restores a
    position is left alone."""
    page = cold()
    page.goto(f"{base_url}/docs/push-items#response")
    settle(page)
    page.evaluate("() => window.scrollTo(0, 300)")
    page.wait_for_timeout(300)
    target = page.evaluate("""() => {
      const t = document.getElementById('response');
      return Math.round(t.getBoundingClientRect().top + window.scrollY);
    }""")
    page.reload()
    settle(page)
    y = page.evaluate("() => Math.round(window.scrollY)")
    # Near where it was, and nowhere near the fragment. Not an exact equality:
    # the font swaps in after restoration and reflows the page under it, which
    # moves the restored offset by a few tens of pixels without the retry having
    # fired at all. What this guards is that the retry did not override the
    # reader's position, and that is a question of which end of the page we are.
    assert abs(y - 300) < 120, f"the restored position moved to {y}, not near 300"
    assert abs(y - target) > 300, f"the fragment retry overrode restoration: {y} vs target {target}"


# ── Finding 2: keyboard access to code that scrolls ──────────────────────────

def test_cold_font_cache_still_names_every_scrollable_block(cold, base_url):
    """The regression this exists for. Measured once at hydration, a cold
    reader got the tab stop on one block out of three, because the font had not
    swapped in yet and the others did not look as though they overflowed."""
    page = cold()
    page.goto(f"{base_url}/docs/push-items")
    settle(page)
    d = page.evaluate(SCROLLABLE)
    assert d["scrolls"] >= 2, f"the fixture no longer has scrolling blocks: {d}"
    assert d["mismatch"] == 0, f"tab stops do not match what overflows: {d}"
    assert d["named"] == d["scrolls"], d
    assert all("scrollable" in l for l in d["labels"]), d


def test_a_warm_context_agrees_with_a_cold_one(browser, base_url):
    """Both directions of the same question: the warm path was the only one an
    earlier suite tested, which is why it passed while cold readers were broken."""
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
    try:
        page = ctx.new_page()
        for _ in range(2):                       # second visit has the font cached
            page.goto(f"{base_url}/docs/push-items")
            settle(page)
            d = page.evaluate(SCROLLABLE)
            assert d["mismatch"] == 0, d
    finally:
        ctx.close()


def test_narrowing_the_viewport_adds_the_tab_stops_it_needs(cold, base_url):
    """Becoming scrollable. Without a resize pass these blocks overflowed with
    no way to reach or pan them from the keyboard."""
    page = cold()
    page.goto(f"{base_url}/docs/push-items")
    settle(page)
    wide = page.evaluate(SCROLLABLE)
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(600)
    narrow = page.evaluate(SCROLLABLE)
    assert narrow["scrolls"] > wide["scrolls"], f"expected more overflow at 390: {wide} -> {narrow}"
    assert narrow["mismatch"] == 0, f"stale after narrowing: {narrow}"


def test_widening_the_viewport_drops_the_tab_stops_it_no_longer_needs(cold, base_url):
    """Ceasing to be scrollable, the other direction."""
    page = cold(width=390, height=844)
    page.goto(f"{base_url}/docs/push-items")
    settle(page)
    narrow = page.evaluate(SCROLLABLE)
    assert narrow["tabbable"] > 0, f"nothing scrolled at 390 to begin with: {narrow}"
    page.set_viewport_size({"width": 1900, "height": 1000})
    page.wait_for_timeout(600)
    wide = page.evaluate(SCROLLABLE)
    assert wide["scrolls"] < narrow["scrolls"], f"{narrow} -> {wide}"
    assert wide["mismatch"] == 0, f"stale after widening: {wide}"


def test_the_label_names_the_language_it_detected(cold, base_url):
    # push-items, because it is the page with blocks wide enough to overflow at
    # this width. failure-library has none at 1440.
    page = cold()
    page.goto(f"{base_url}/docs/push-items")
    settle(page)
    labels = page.evaluate(
        "() => [...document.querySelectorAll('.docs-pre code[aria-label]')]"
        ".map(e => [e.dataset.lang, e.getAttribute('aria-label')])")
    assert labels, "no scrollable block on this page to name"
    for lang, label in labels:
        assert label == f"{'Code' if lang in (None, 'text') else lang} example, scrollable", (lang, label)


def test_the_scrollable_ring_is_visible(cold, base_url):
    page = cold()
    page.goto(f"{base_url}/docs/push-items")
    settle(page)
    d = page.evaluate("""() => {
      const el = document.querySelector('.docs-pre code[tabindex]');
      el.focus();
      const cs = getComputedStyle(el);
      return { focused: document.activeElement === el, width: cs.outlineWidth,
               style: cs.outlineStyle, offset: cs.outlineOffset };
    }""")
    assert d["focused"] and d["style"] != "none" and float(d["width"].rstrip("px")) >= 2, d
    # inset, so `overflow: hidden` on the pre cannot clip it
    assert float(d["offset"].rstrip("px")) < 0, d


# ── Finding 3 coverage: the rail across client-side navigation ───────────────

def test_the_rail_is_rebuilt_not_accumulated_across_navigation(cold, base_url):
    """Without the pathname dependency the rail kept the first page's headings
    and every link in it pointed at an id no longer in the document. Without the
    cleanup the entries accumulated instead."""
    page = cold()
    page.goto(f"{base_url}/docs/push-items")
    settle(page)
    first = page.evaluate(PAGE_STATE)["tocText"]

    hops = [("Webhooks", "webhooks"), ("Poll results", "poll-results"),
            ("Task config", "task-config"), ("Quickstart", "quickstart"),
            ("Errors and notes", "errors"), ("Push items", "push-items")]
    for label, slug in hops:
        page.click(f".docs-nav a:has-text('{label}')")
        page.wait_for_url(f"**/docs/{slug}")
        page.wait_for_timeout(500)
        d = page.evaluate(PAGE_STATE)
        expected = d["heads"] if d["heads"] > 1 else 0
        assert d["tocDead"] == 0, f"{label}: dead rail links {d}"
        assert d["toc"] == expected, f"{label}: rail has {d['toc']}, page has {d['heads']}: {d}"
        assert d["tocTitles"] == (1 if expected else 0), f"{label}: {d['tocTitles']} titles: {d}"
        assert d["withId"] == d["heads"], f"{label}: ids not rebuilt {d}"
        assert d["strips"] == d["pres"], f"{label}: code headers not rebuilt {d}"
        if label == "Webhooks":
            assert d["tocText"] != first, "the rail still shows the previous page"


def test_navigating_away_does_not_drag_a_stale_hash(cold, base_url):
    page = cold()
    page.goto(f"{base_url}/docs/push-items")
    settle(page)
    page.click(".docs-toc a:has-text('Response')")
    page.wait_for_timeout(500)
    assert page.evaluate("() => Math.round(window.scrollY)") > 0
    page.click(".docs-nav a:has-text('Quickstart')")
    page.wait_for_url("**/docs/quickstart")
    page.wait_for_timeout(600)
    assert page.evaluate("() => Math.round(window.scrollY)") == 0


def test_listeners_and_observers_do_not_accumulate(cold, base_url):
    page = cold()
    page.add_init_script("""
      window.__lc = { add: 0, rm: 0, io: 0, dis: 0 };
      const a = document.addEventListener.bind(document);
      const r = document.removeEventListener.bind(document);
      document.addEventListener = (...x) => { if (x[0] === 'keydown') window.__lc.add++; return a(...x) };
      document.removeEventListener = (...x) => { if (x[0] === 'keydown') window.__lc.rm++; return r(...x) };
      // Counted only for the rail's own observer: next/link registers its own
      // for prefetching and never disconnects them, so counting every
      // constructor would be asserting on the framework, not on this code.
      const MINE = '-64px 0px -70% 0px';
      const IO = window.IntersectionObserver;
      window.IntersectionObserver = class extends IO {
        constructor(cb, opts) {
          super(cb, opts);
          this.__mine = !!opts && opts.rootMargin === MINE;
          if (this.__mine) window.__lc.io++;
        }
        disconnect() { if (this.__mine) window.__lc.dis++; return super.disconnect() }
      };
    """)
    page.goto(f"{base_url}/docs/push-items")
    settle(page)
    baseline = page.evaluate("() => window.__lc.add - window.__lc.rm")
    for label, slug in [("Webhooks", "webhooks"), ("Quickstart", "quickstart"),
                        ("Push items", "push-items"), ("Poll results", "poll-results")]:
        page.click(f".docs-nav a:has-text('{label}')")
        page.wait_for_url(f"**/docs/{slug}")
        page.wait_for_timeout(450)
    lc = page.evaluate("() => window.__lc")
    assert lc["add"] - lc["rm"] == baseline, f"keydown listeners accumulated: {lc}"
    assert lc["io"] >= 2, f"the rail observer was not seen at all: {lc}"
    assert lc["io"] - lc["dis"] <= 1, f"rail observers left connected: {lc}"


# ── Every page, and the things that must hold on all of them ─────────────────

@pytest.mark.parametrize("slug", PAGES)
def test_each_page_renders_and_enhances(cold, base_url, slug):
    page = cold()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    resp = page.goto(f"{base_url}/docs/{slug}")
    settle(page)
    d = page.evaluate(PAGE_STATE)
    assert resp.status == 200
    assert not errors, errors
    assert d["withId"] == d["heads"], f"/docs/{slug}: ids missing {d}"
    assert d["strips"] == d["pres"], f"/docs/{slug}: code headers missing {d}"
    assert d["tocDead"] == 0, f"/docs/{slug}: dead rail links {d}"
    # heading hierarchy: h1 then h2s, never a skipped level
    levels = page.evaluate("() => [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].map(h => +h.tagName[1])")
    assert all(b - a <= 1 for a, b in zip(levels, levels[1:])), f"/docs/{slug}: skipped level in {levels}"


# ── Copy ────────────────────────────────────────────────────────────────────

def test_copy_hands_over_the_original_text(browser, base_url):
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000},
                              permissions=["clipboard-read", "clipboard-write"])
    try:
        page = ctx.new_page()
        page.goto(f"{base_url}/docs/webhooks")
        settle(page)
        page.click(".docs-pre .copy-btn")
        page.wait_for_timeout(400)
        copied = page.evaluate("() => navigator.clipboard.readText()")
        shown = page.evaluate("() => document.querySelector('.docs-pre code').textContent")
        assert copied == shown, "the copy did not match the block"
        assert "<span" not in copied, "the marked-up version was copied"
        assert "<hex>" in copied, "the original angle brackets were lost"
        # text_content, not inner_text: the button is text-transform: uppercase
        assert page.locator(".docs-pre .copy-btn").first.text_content().strip() == "Copied"
    finally:
        ctx.close()


def test_markup_inside_an_example_is_escaped_in_the_dom(cold, base_url):
    page = cold()
    page.goto(f"{base_url}/docs/webhooks")
    settle(page)
    html = page.locator(".docs-pre").first.inner_html()
    assert "&lt;hex&gt;" in html
    assert "<hex>" not in html


# ── Mobile ──────────────────────────────────────────────────────────────────

def test_mobile_390_layout(cold, base_url):
    page = cold(width=390, height=844)
    page.goto(f"{base_url}/docs/push-items")
    settle(page)
    d = page.evaluate("""() => {
      const bar = document.querySelector('.docs-topbar').getBoundingClientRect();
      const low = Math.max(...[...document.querySelectorAll('.docs-topbar-in > *')]
                    .map(e => e.getBoundingClientRect().bottom));
      const nav = document.querySelector('.docs-nav a').getBoundingClientRect();
      const rail = document.querySelector('.docs-toc');
      return { sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
               contentBottom: Math.round(low), navTop: Math.round(nav.top),
               rail: getComputedStyle(rail).display, railInTree: rail.offsetParent !== null,
               token: getComputedStyle(document.querySelector('.docs-shell'))
                        .getPropertyValue('--d-topbar').trim() };
    }""")
    assert d["sw"] == d["cw"], f"horizontal scroll at 390: {d}"
    assert d["contentBottom"] <= d["navTop"], f"the sticky bar overlaps the navigation: {d}"
    assert d["rail"] == "none" and not d["railInTree"], d
    assert d["token"] == "100px", f"the anchor offset token did not grow for the wrapped bar: {d}"


def test_mobile_390_anchor_clears_the_wrapped_bar(cold, base_url):
    page = cold(width=390, height=844)
    page.goto(f"{base_url}/docs/push-items#response")
    settle(page)
    d = page.evaluate("""() => {
      const t = document.getElementById('response');
      const b = document.querySelector('.docs-topbar').getBoundingClientRect();
      return { top: Math.round(t.getBoundingClientRect().top), bar: Math.round(b.bottom) };
    }""")
    assert d["top"] >= d["bar"], d


# ── Keyboard and contrast ───────────────────────────────────────────────────

def test_every_tab_stop_shows_a_ring(cold, base_url):
    page = cold()
    page.goto(f"{base_url}/docs/push-items")
    settle(page)
    without, stops = [], 0
    for _ in range(40):
        page.keyboard.press("Tab")
        d = page.evaluate("""() => { const e = document.activeElement;
          if (!e || e === document.body) return null;
          const cs = getComputedStyle(e);
          return { tag: e.tagName, cls: e.className, w: cs.outlineWidth, st: cs.outlineStyle }; }""")
        if d is None:
            break
        stops += 1
        if d["st"] == "none" or float(d["w"].rstrip("px")) < 2:
            without.append(f"{d['tag']}.{d['cls'][:20]} ({d['w']} {d['st']})")
    assert stops > 10, f"only {stops} tab stops found"
    assert not without, f"tab stops with no visible ring: {without}"


def test_following_a_rail_link_moves_the_keyboard_on(cold, base_url):
    """Tab after the jump should continue from the target, not the page top."""
    page = cold()
    page.goto(f"{base_url}/docs/push-items")
    settle(page)
    page.click(".docs-toc a:has-text('Bulk (data in your storage)')")
    page.wait_for_timeout(500)
    page.keyboard.press("Tab")
    y = page.evaluate("() => Math.round(document.activeElement.getBoundingClientRect().top)")
    assert 0 <= y <= 400, f"focus continued from {y}, not from the jumped-to section"


@pytest.mark.parametrize("selector,minimum", [
    (".docs-toc a", 4.5),
    (".docs-toc-t", 4.5),
    (".docs-pre .copy-btn", 4.5),     # at rest, with no hover: a touch screen has none
    (".docs-pre .pre-lang", 4.5),
    (".docs-pre code", 4.5),
    (".docs-nav a", 4.5),
    (".docs-main p", 4.5),
])
def test_text_meets_wcag_aa_contrast(cold, base_url, selector, minimum):
    page = cold()
    page.goto(f"{base_url}/docs/push-items")
    settle(page)
    d = page.evaluate(CONTRAST, selector)
    assert d is not None, f"{selector} not present"
    need = 3.0 if d["size"] >= 18.66 else minimum
    assert d["ratio"] >= need, f"{selector} is {d['ratio']}:1 at {d['size']}px, needs {need}"


def test_the_active_rail_link_is_distinguishable(cold, base_url):
    page = cold()
    page.goto(f"{base_url}/docs/push-items")
    settle(page)
    page.evaluate("() => document.querySelector('.docs-toc a').classList.add('active')")
    page.wait_for_timeout(500)            # let the colour transition finish
    d = page.evaluate(CONTRAST, ".docs-toc a.active")
    assert d["ratio"] >= 4.5, d


def test_reduced_motion_is_honoured(browser, base_url):
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
    try:
        page = ctx.new_page()
        page.goto(f"{base_url}/docs/push-items")
        settle(page)
        d = page.evaluate("""() => ({
          toc: getComputedStyle(document.querySelector('.docs-toc a')).transitionDuration,
          btn: getComputedStyle(document.querySelector('.docs-pre .copy-btn')).transitionDuration })""")
        assert d["toc"] == "0s" and d["btn"] == "0s", d
    finally:
        ctx.close()


# ── Shared stylesheet ───────────────────────────────────────────────────────

def test_the_developers_route_sharing_docs_css_is_unaffected(cold, base_url):
    """/developers imports ../docs/docs.css and uses .docs-main, so a change to
    a shared selector reaches it. Nothing the refinement removed or restyled has
    an element to hit there, and this is what says so."""
    page = cold()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base_url}/developers")
    settle(page)
    d = page.evaluate("""() => {
      const shell = document.querySelector('.docs-shell');
      return { tables: document.querySelectorAll('.docs-main table').length,
               pres: document.querySelectorAll('.docs-pre').length,
               heads: document.querySelectorAll('.docs-main h2, .docs-main h3').length,
               toc: document.querySelectorAll('.docs-toc').length,
               token: shell ? getComputedStyle(shell).getPropertyValue('--d-topbar').trim() : null };
    }""")
    assert not errors, errors
    assert d["token"] == "57px", f"--d-topbar does not resolve on /developers: {d}"
    assert d["tables"] == d["pres"] == d["heads"] == d["toc"] == 0, d
