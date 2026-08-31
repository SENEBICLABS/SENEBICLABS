"""
Playwright smoke test for the public site (https://senebiclabs.com).
Run: PYTHONPATH=. pytest tests/e2e/test_public_site.py --browser chromium
(Requires network + the Chromium binary from `playwright install chromium`.)
"""
import re
from playwright.sync_api import Page, expect

BASE = "https://senebiclabs.com"


def test_site_loads_with_title_and_hero(page: Page):
    page.goto(BASE, wait_until="domcontentloaded")
    expect(page).to_have_title(re.compile("Senebiclabs"))
    expect(page.get_by_role("heading",
                             name=re.compile("Clinician-grade data for medical AI", re.I))).to_be_visible()


def test_primary_nav_and_cta_present(page: Page):
    page.goto(BASE, wait_until="domcontentloaded")
    for label in ["Docs", "About"]:
        expect(page.get_by_role("link", name=label).first).to_be_visible()
    demo = page.get_by_role("link", name=re.compile("Book a demo", re.I)).first
    expect(demo).to_be_visible()
    expect(demo).to_have_attribute("href", re.compile(r"/submit"))


def test_about_link_navigates(page: Page):
    page.goto(BASE, wait_until="domcontentloaded")
    page.get_by_role("link", name="About").first.click()
    expect(page).to_have_url(re.compile(r"/about"))
