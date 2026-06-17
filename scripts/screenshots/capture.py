"""Drive a running app instance with headless Chromium and capture screenshots.

Assumes the app is already serving on ``--base-url`` against the demo database
built by ``seed_demo.py``. Logs in as an annotator, then visits each route and
writes a jpg into ``--out-dir`` (default ``userdocs/img``).

Selenium is pulled in ephemerally by the orchestrator via ``uv run --with
selenium``; nothing is added to the project dependencies. Chromium and
chromedriver are expected on PATH.
"""

from __future__ import annotations

import argparse
import base64
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# (route, output filename, optional CSS selector to wait for before shooting)
SHOTS = [
    ("/overview", "dashboard.jpg", ".q-page, .q-card"),
    ("/review", "reviewing.jpg", "video, .q-card"),
    ("/model-import", "importing.jpg", ".q-tab, .q-card"),
    ("/settings", "settings.jpg", ".q-card"),
]


def save_as_jpg(driver: webdriver.Chrome, path: Path | str, quality: int = 80) -> None:
    """Capture a JPEG screenshot using Chrome DevTools Protocol."""
    res = driver.execute_cdp_cmd("Page.captureScreenshot", {"format": "jpeg", "quality": quality})
    with open(path, "wb") as f:
        f.write(base64.b64decode(res["data"]))


def build_driver(width: int, height: int) -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--hide-scrollbars")
    opts.add_argument(f"--window-size={width},{height}")
    opts.add_argument("--force-device-scale-factor=1")
    opts.binary_location = _chromium_path()
    return webdriver.Chrome(service=Service(), options=opts)


def _chromium_path() -> str:
    import shutil

    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("No Chromium/Chrome binary found on PATH")


def login(driver: webdriver.Chrome, base_url: str, name: str, out_dir: Path | None = None) -> None:
    driver.get(f"{base_url}/login")
    wait = WebDriverWait(driver, 20)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input")))
    time.sleep(1.0)
    if out_dir is not None:
        # The login / annotator-selection screen, before any name is entered.
        out_dir.mkdir(parents=True, exist_ok=True)
        save_as_jpg(driver, str(out_dir / "login.jpg"))
        print("  ✓ /login -> login.jpg")
    # Quasar renders several inputs (some hidden); use the first visible one.
    field = next(
        el
        for el in driver.find_elements(By.CSS_SELECTOR, "input")
        if el.is_displayed() and el.is_enabled()
    )
    driver.execute_script("arguments[0].scrollIntoView(true);", field)
    field.click()
    field.send_keys(name)
    time.sleep(0.5)
    field.send_keys(Keys.ENTER)  # commit the typed/selected value
    time.sleep(0.5)

    def _click_continue():
        for b in driver.find_elements(By.CSS_SELECTOR, "button"):
            if b.text.strip().lower() in ("continue", "continuer"):
                b.click()
                return True
        return False

    _click_continue()
    try:
        wait.until(lambda d: "/login" not in d.current_url)
    except Exception:
        # Last resort: re-commit and submit via the field's Enter handler.
        field.send_keys(Keys.ENTER)
        _click_continue()
        WebDriverWait(driver, 10).until(lambda d: "/login" not in d.current_url)
    time.sleep(1.0)


def dismiss_overlays(driver: webdriver.Chrome) -> None:
    """Close the first-run onboarding tour if it is showing."""
    skip_labels = {"skip tour", "skip", "passer le tour", "passer", "got it", "close"}
    for b in driver.find_elements(By.CSS_SELECTOR, "button"):
        try:
            if b.is_displayed() and b.text.strip().lower() in skip_labels:
                b.click()
                time.sleep(0.6)
                return
        except Exception:
            continue


def capture_tour(driver: webdriver.Chrome, base_url: str, out_dir: Path) -> None:
    """Screenshot the first-run guided tour overlay on the review page.

    Must run before :func:`capture` dismisses it (dismissal is persisted)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    driver.get(f"{base_url}/review")
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".q-card, video"))
    )
    time.sleep(2.5)  # let the tour dialog animate in
    if driver.find_elements(By.CSS_SELECTOR, ".tour-dialog"):
        save_as_jpg(driver, str(out_dir / "onboarding-tour.jpg"))
        print("  ✓ /review (tour) -> onboarding-tour.jpg")
    else:
        print("  ! tour overlay not present, skipping onboarding-tour.jpg")


def capture(driver: webdriver.Chrome, base_url: str, out_dir: Path, settle: float) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    wait = WebDriverWait(driver, 20)
    for route, filename, selector in SHOTS:
        driver.get(f"{base_url}{route}")
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
        except Exception:
            print(f"  ! selector {selector!r} not found on {route}, shooting anyway")
        time.sleep(1.0)
        dismiss_overlays(driver)
        time.sleep(settle)
        target = out_dir / filename
        save_as_jpg(driver, str(target))
        print(f"  ✓ {route} -> {target}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--out-dir", default="userdocs/img")
    ap.add_argument("--annotator", default="alice")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=1100)
    ap.add_argument(
        "--settle", type=float, default=2.5, help="seconds to wait after load before each shot"
    )
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    driver = build_driver(args.width, args.height)
    try:
        login(driver, args.base_url, args.annotator, out_dir=out_dir)
        capture_tour(driver, args.base_url, out_dir)
        capture(driver, args.base_url, out_dir, args.settle)
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
