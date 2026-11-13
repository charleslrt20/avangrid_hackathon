"""Capture deck screenshots of the running Streamlit app (task 2.5) into docs/figures/app_*.png.

    streamlit run app/streamlit_app.py --server.port 8501 --server.headless true &
    python app/screenshots.py [--url http://localhost:8501]

Uses Playwright with the installed Google Chrome. Customer IDs stay hidden (the app's default), so the
images only show anonymised aliases.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

OUT = Path(__file__).resolve().parents[1] / "docs" / "figures"


def settle(page: Page, ms: int = 2500) -> None:
    page.wait_for_timeout(400)
    page.wait_for_selector("[data-testid='stStatusWidget']", state="detached", timeout=30000)
    page.wait_for_timeout(ms)


def view(page: Page, name: str) -> None:
    page.locator("[data-testid='stSidebar']").get_by_text(name, exact=True).click()
    settle(page)


def shot(page: Page, name: str) -> None:
    path = OUT / f"app_{name}.png"
    page.screenshot(path=str(path), full_page=False)
    print(path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8501")
    url = ap.parse_args().url
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        page.goto(url)
        page.wait_for_selector("text=AMI electrification", timeout=60000)
        settle(page, 3000)
        shot(page, "overview")

        view(page, "Customer lookup")
        page.get_by_role("tab", name="Battery dispatch").click()
        settle(page)
        page.get_by_role("tab", name="Battery dispatch").scroll_into_view_if_needed()
        page.mouse.wheel(0, 380)
        settle(page, 1500)
        shot(page, "dispatch_summer")
        page.get_by_text("winter", exact=True).first.click()
        settle(page)
        shot(page, "dispatch_winter")

        page.mouse.wheel(0, -2000)
        page.get_by_role("tab", name="Load carpet").click()
        settle(page, 1500)
        shot(page, "customer_lookup")
        page.get_by_role("tab", name="Why flagged").click()
        settle(page, 1500)
        shot(page, "why_flagged")

        view(page, "Segment explorer")
        shot(page, "segment_explorer")

        view(page, "Battery ranking")
        shot(page, "battery_ranking")
        browser.close()


if __name__ == "__main__":
    main()
