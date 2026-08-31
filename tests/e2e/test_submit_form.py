"""
Public intake form E2E (https://senebiclabs.com/submit) — the client's front door.

Fills and submits the "Book a demo" form in a real browser, asserts the success
state, verifies the lead reached the backend (project_submissions), then deletes it.

OPT-IN: a real submit creates a lead row AND sends emails (a confirmation to the
address used, plus an admin alert). So it is skipped unless RUN_SUBMIT_TEST=1, to
keep CI and casual runs from creating leads / sending mail.

Run:  RUN_SUBMIT_TEST=1 PYTHONPATH=. pytest tests/e2e/test_submit_form.py --browser chromium
"""
import os
import time

import pytest
from playwright.sync_api import Page, expect

BASE = "https://senebiclabs.com"
HEALTH = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytestmark = pytest.mark.skipif(
    not (os.environ.get("RUN_SUBMIT_TEST") == "1" and os.path.exists(f"{HEALTH}/.env")),
    reason="opt-in (creates a real lead + sends emails); set RUN_SUBMIT_TEST=1 to run",
)


def _envv(key):
    for line in open(f"{HEALTH}/.env"):
        if line.startswith(key + "="):
            return line.rstrip("\n").split("=", 1)[1]
    raise KeyError(key)


def test_submit_form_creates_a_lead(page: Page):
    import httpx
    SB = _envv("SUPABASE_URL").rstrip("/")
    SBK = _envv("SUPABASE_SERVICE_KEY")
    hdr = {"apikey": SBK, "Authorization": f"Bearer {SBK}"}

    ts = int(time.time())
    email = f"e2e-submit-{ts}@senebiclabs.com"
    company = f"E2E Submit Test {ts}"

    # ── fill + submit the real form ──
    page.goto(BASE + "/submit", wait_until="networkidle")
    page.fill("#firstName", "E2E")
    page.fill("#lastName", "Tester")
    page.fill("#email", email)
    page.fill("#company", company)
    page.fill("#jobTitle", "QA")
    page.fill("#description", "Automated submit-form E2E test — please ignore.")
    cb = page.locator('input[type=checkbox]').first
    if cb.count():
        cb.check()
    page.get_by_role("button", name="Book a demo").click()

    # ── success state ──
    expect(page.get_by_text("Got it", exact=False)).to_be_visible(timeout=15000)

    # ── verify the lead reached the backend, then remove it ──
    try:
        rows = httpx.get(f"{SB}/rest/v1/project_submissions?email=eq.{email}&select=id,company",
                         headers=hdr, timeout=30).json()
        assert isinstance(rows, list) and len(rows) == 1, f"expected 1 lead, got {rows}"
        assert rows[0]["company"] == company
    finally:
        httpx.request("DELETE", f"{SB}/rest/v1/project_submissions?email=eq.{email}",
                      headers={**hdr, "Prefer": "return=minimal"}, timeout=30)
