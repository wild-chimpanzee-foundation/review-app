"""Capture the first-run setup wizard against an *empty* database.

The wizard lives on a single ``/setup`` page whose steps are shown/hidden in
place, so we drive it with clicks (Next -> Start Fresh -> project form -> bundle
tab) and screenshot each state. Run by the orchestrator's "phase A"; expects an
app started against a data dir with no database yet.
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


def build_driver(width: int, height: int) -> webdriver.Chrome:
    import shutil

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--hide-scrollbars")
    opts.add_argument(f"--window-size={width},{height}")
    opts.add_argument("--force-device-scale-factor=1")
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        if shutil.which(name):
            opts.binary_location = shutil.which(name)
            break
    return webdriver.Chrome(service=Service(), options=opts)


def click_contains(driver, selector: str, text: str) -> bool:
    """Click the first visible ``selector`` element whose text contains ``text``.

    Substring (not equality) because buttons carry icon text and cards/tabs
    include captions, e.g. a Next button reads "arrow_forward\\nNext"."""
    target = text.strip().lower()
    for el in driver.find_elements(By.CSS_SELECTOR, selector):
        try:
            if el.is_displayed() and target in el.text.strip().lower():
                el.click()
                return True
        except Exception:
            continue
    return False


def login(driver, base_url: str, name: str) -> None:
    """On a fresh install the annotator name is asked before the wizard."""
    driver.get(f"{base_url}/login")
    wait = WebDriverWait(driver, 20)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input")))
    time.sleep(0.8)
    field = next(
        e
        for e in driver.find_elements(By.CSS_SELECTOR, "input")
        if e.is_displayed() and e.is_enabled()
    )
    field.click()
    field.send_keys(name)
    time.sleep(0.5)
    field.send_keys(Keys.ENTER)
    time.sleep(0.5)
    for b in driver.find_elements(By.CSS_SELECTOR, "button"):
        if b.text.strip().lower() in ("continue", "continuer"):
            b.click()
            break
    wait.until(lambda d: "/login" not in d.current_url)
    time.sleep(0.8)


def _fill_by_placeholder(driver, placeholder_substr: str, value: str) -> bool:
    """Type ``value`` into the visible input whose placeholder contains the given
    substring (Quasar renders the placeholder on the native <input>)."""
    for el in driver.find_elements(By.CSS_SELECTOR, "input"):
        try:
            ph = el.get_attribute("placeholder") or ""
            if el.is_displayed() and placeholder_substr.lower() in ph.lower():
                el.click()
                el.send_keys(value)
                return True
        except Exception:
            continue
    return False


def shoot(driver, out_dir: Path, name: str, settle: float = 1.2, quality: int = 80) -> None:
    time.sleep(settle)
    path = out_dir / name
    res = driver.execute_cdp_cmd("Page.captureScreenshot", {"format": "jpeg", "quality": quality})
    with open(path, "wb") as f:
        f.write(base64.b64decode(res["data"]))
    print(f"  ✓ {name}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8099")
    ap.add_argument("--out-dir", default="userdocs/img")
    ap.add_argument(
        "--video-dir",
        default="/home/researcher/camera-videos/PSS_2024",
        help="illustrative path typed into the project form (need not exist)",
    )
    ap.add_argument("--project-name", default="PSS 2024")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=1100)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    driver = build_driver(args.width, args.height)
    wait = WebDriverWait(driver, 20)
    try:
        # Fresh install asks for the annotator name first, then the wizard.
        login(driver, args.base_url, "Sam")

        # Step 1: welcome / language / ffmpeg check.
        driver.get(f"{args.base_url}/setup")
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".q-card")))
        # The Next button stays disabled until the ffmpeg probe finishes.
        wait.until(
            lambda d: any(
                b.is_enabled() and ("next" in b.text.lower() or "suivant" in b.text.lower())
                for b in d.find_elements(By.CSS_SELECTOR, "button")
            )
        )
        shoot(driver, out_dir, "onboarding-welcome.jpg")

        # Step 1.5: fresh start vs restore.
        click_contains(driver, "button", "Next")
        shoot(driver, out_dir, "onboarding-start-choice.jpg")

        # Step 2: project creation form (manual tab is default). The fresh/restore
        # options are clickable cards (".cursor-pointer"), not buttons.
        click_contains(driver, ".cursor-pointer", "Start Fresh")
        time.sleep(0.6)
        # Fill name + video dir (targeted by placeholder) for an illustrative form.
        # The path need not exist — the Sync button only requires both fields set.
        _fill_by_placeholder(driver, "PSS 2024", args.project_name)
        _fill_by_placeholder(driver, "/path/to/videos", args.video_dir)
        time.sleep(0.5)
        shoot(driver, out_dir, "onboarding-new-project.jpg")

        # Bundle import tab.
        if click_contains(driver, ".q-tab", "Import from bundle"):
            time.sleep(0.5)
            shoot(driver, out_dir, "onboarding-bundle.jpg")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
