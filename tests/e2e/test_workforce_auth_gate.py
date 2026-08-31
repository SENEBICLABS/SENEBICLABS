"""
Playwright auth-gate tests for the workforce app (https://app.senebiclabs.com).
Security invariant: no clinician data reaches a browser without a valid session.
Run: PYTHONPATH=. pytest tests/e2e/test_workforce_auth_gate.py --browser chromium
"""
import re
from playwright.sync_api import Page, expect

APP = "https://app.senebiclabs.com"


def test_root_redirects_unauthenticated_to_login(page: Page):
    page.goto(APP + "/", wait_until="networkidle")
    expect(page).to_have_url(re.compile(r"/login"))
    expect(page.get_by_text(re.compile("Sign in", re.I)).first).to_be_visible()


def test_login_is_invite_only(page: Page):
    page.goto(APP + "/login", wait_until="networkidle")
    expect(page.get_by_text(re.compile("invite-only", re.I)).first).to_be_visible()


def test_api_returns_401_without_session(page: Page):
    # the definitive no-data proof: the data API refuses without a token
    resp = page.request.get(APP + "/api/pools")
    assert resp.status == 401, resp.status


def test_dashboard_exposes_no_session_or_data(page: Page):
    page.goto(APP + "/dashboard", wait_until="networkidle")
    body = page.inner_text("body").lower()
    assert "sign out" not in body and "log out" not in body      # no active session leaked in
    assert "unauthorized" in body or "/login" in page.url        # data did not load / bounced


def test_dashboard_redirects_logged_out_to_login(page: Page):
    # Fixed in the workforce repo (edge middleware guard, commit 1cf006a): a
    # logged-out visitor is redirected to sign-in rather than shown a broken shell.
    page.goto(APP + "/dashboard", wait_until="networkidle")
    expect(page).to_have_url(re.compile(r"/login"))
