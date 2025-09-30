import re
import time
import csv
import sys
import argparse
from pathlib import Path
from typing import List, Dict, Optional

import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import NoSuchElementException, ElementClickInterceptedException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager


# ---------- Utility parsing helpers ----------

RATING_RE = re.compile(r"bubble_(\d+)")
DATE_CLEAN_RE = re.compile(r"Date of experience:\s*", re.I)

def parse_rating_from_classes(class_list: List[str]) -> Optional[float]:
    """
    TripAdvisor encodes rating in classes like 'ui_bubble_rating bubble_50'.
    50 -> 5.0, 40 -> 4.0, etc.
    """
    if not class_list:
        return None
    for cls in class_list:
        m = RATING_RE.search(cls)
        if m:
            val = m.group(1)
            try:
                return float(val) / 10.0
            except Exception:
                return None
    return None


def extract_text(el) -> str:
    """Safely get all text from a selenium WebElement."""
    if el is None:
        return ""
    try:
        return el.text.strip()
    except Exception:
        return ""


# ---------- Core scraper ----------

from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

def get_driver(headless: bool = True) -> webdriver.Chrome:
    chrome_options = Options()
    if headless:
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1400,900")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--lang=en-US")
    chrome_options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    )

    # ✅ Use Service with ChromeDriverManager
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.set_page_load_timeout(60)
    return driver



def dismiss_overlays(driver):
    """Try to close cookie banners or sign-in modals if they appear."""
    # Common cookie consent buttons:
    possible_selectors = [
        "button[aria-label*='Accept']",
        "button[aria-label*='agree']",
        "button:contains('Accept')",  # not valid css, but we try others below
        "button[title*='Accept']",
    ]
    # Try a few known buttons
    for sel in ["button[aria-label='Accept all']", "button[aria-label='Accept All']",
                "button[aria-label='Accept']", "button[aria-label='I Accept']"]:
        try:
            btn = driver.find_element(By.CSS_SELECTOR, sel)
            btn.click()
            time.sleep(1)
            break
        except NoSuchElementException:
            pass
        except ElementClickInterceptedException:
            pass

    # Close sign-in popups if any
    try:
        close_btns = driver.find_elements(By.CSS_SELECTOR, "button[aria-label='Close']")
        for b in close_btns:
            try:
                b.click()
                time.sleep(0.5)
            except Exception:
                continue
    except Exception:
        pass


def click_read_more_in_card(driver, card):
    """Expand truncated reviews if 'Read more' exists inside a card."""
    try:
        # Buttons with text like 'Read more' or 'More'
        more_buttons = card.find_elements(By.XPATH, ".//span[contains(., 'Read more') or contains(., 'More')]")
        for mb in more_buttons:
            try:
                driver.execute_script("arguments[0].click();", mb)
                time.sleep(0.3)
            except Exception:
                continue
    except Exception:
        pass


def find_review_cards(driver) -> List:
    """
    Find review cards in TripAdvisor pages with multiple fallback selectors.
    """
    selectors = [
        "div.YibKl",                 # ✅ Uluru + new TripAdvisor layout
        "[data-test-target='review-card']",
        "[data-test-target='HR_CC_CARD']",
        "div[data-reviewid]",
        "section.review-container",
        "article"                    # fallback
    ]
    for sel in selectors:
        try:
            candidates = driver.find_elements(By.CSS_SELECTOR, sel)
            if candidates:
                return candidates
        except Exception:
            continue
    return []



def extract_review_from_card(card) -> Dict:
    """
    Extract title, text, rating, date, reviewer origin if present.
    Uses multiple fallbacks for robustness.
    """
    review = {
        "title": "",
        "text": "",
        "rating": None,
        "date": "",
        "reviewer_origin": ""
    }

    # Rating
    try:
        star = card.find_element(By.CSS_SELECTOR, "[class*='ui_bubble_rating']")
        review["rating"] = parse_rating_from_classes(star.get_attribute("class").split())
    except Exception:
        review["rating"] = None

    # Title
    for sel in [
        "[data-test-target='review-title']",
        "a[href*='#REVIEWS']",
        "a[role='button'] span",
        "span[class*='title']"
    ]:
        try:
            el = card.find_element(By.CSS_SELECTOR, sel)
            if extract_text(el):
                review["title"] = extract_text(el)
                break
        except Exception:
            continue

    # Text
    # TripAdvisor often uses <q> tag for main review text or data-test-target='review-text'
    text_candidates = [
        ".//q",  # main body
        ".//*[@data-test-target='review-text']",
        ".//span[@class and string-length(normalize-space(text()))>0]"
    ]
    for xp in text_candidates:
        try:
            els = card.find_elements(By.XPATH, xp)
            # Choose the longest text blob
            strings = [extract_text(e) for e in els if extract_text(e)]
            if strings:
                review["text"] = max(strings, key=len)
                if review["text"]:
                    break
        except Exception:
            continue

    # Date (often in "Date of experience: Month Year")
    try:
        date_els = card.find_elements(By.XPATH, ".//*[contains(., 'Date of experience')]")
        if date_els:
            date_txt = extract_text(date_els[0])
            review["date"] = DATE_CLEAN_RE.sub("", date_txt)
    except Exception:
        pass

    # Reviewer origin (often near the user name / location)
    try:
        # Try a few heuristics: look for country/state strings near avatar/name lines
        possible = card.find_elements(By.XPATH, ".//*[contains(@class, 'location')] | .//*[contains(@class,'HsxE')]")
        for el in possible:
            txt = extract_text(el)
            if txt and len(txt) < 60 and any(c.isalpha() for c in txt):
                review["reviewer_origin"] = txt
                break
    except Exception:
        pass

    return review


def go_to_next_page(driver) -> bool:
    """
    Click 'Next' pagination button if present.
    Returns True if navigated to next page, else False.
    """
    # Newer TA uses 'Next' button with aria-label, sometimes as <a>, sometimes <button>
    selectors = [
        "a[aria-label*='Next']",
        "button[aria-label*='Next']",
        "a.ui_button.nav.next",
    ]
    for sel in selectors:
        try:
            next_btn = driver.find_element(By.CSS_SELECTOR, sel)
            if "disabled" in (next_btn.get_attribute("class") or "").lower():
                return False
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_btn)
            time.sleep(0.3)
            next_btn.click()
            time.sleep(1.2)  # give time to load
            return True
        except NoSuchElementException:
            continue
        except ElementClickInterceptedException:
            # try JS click
            try:
                driver.execute_script("arguments[0].click();", next_btn)
                time.sleep(1.0)
                return True
            except Exception:
                continue
        except Exception:
            continue
    return False


def scrape_tripadvisor(url: str,
                       track: str,
                       attraction_name: str,
                       max_pages: int = 3,
                       polite_delay: float = 1.0,
                       headless: bool = True) -> pd.DataFrame:
    """
    Scrape up to `max_pages` of reviews from a TripAdvisor attraction page.
    """
    driver = get_driver(headless=headless)
    driver.get(url)
    time.sleep(2.0)

    dismiss_overlays(driver)

    rows = []
    page_num = 1

    while page_num <= max_pages:
        # Wait until some reviews render
        try:
            WebDriverWait(driver, 15).until(
                EC.any_of(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "[data-test-target='review-card']")),
                    EC.presence_of_element_located((By.CSS_SELECTOR, "[data-test-target='HR_CC_CARD']"))
                )
            )
        except TimeoutException:
            # try to continue anyway
            pass

        # DEBUG: dump part of the page to inspect
        html_snippet = driver.page_source[:5000]  # first 5000 chars only
        with open("debug_uluru.html", "w", encoding="utf-8") as f:
            f.write(html_snippet)
        print("✅ Saved debug_uluru.html (first part of page) for inspection")


        cards = find_review_cards(driver)

        for c in cards:
            click_read_more_in_card(driver, c)
            data = extract_review_from_card(c)
            if not data["text"]:
                continue
            rows.append({
                "track": track,
                "source": "TripAdvisor",
                "attraction": attraction_name,
                "review_text": data["text"],
                "rating": data["rating"],
                "review_date": data["date"],
                "reviewer_origin": data["reviewer_origin"],
                "lat": "",
                "lon": "",
                "url": url
            })

        print(f"[page {page_num}] scraped {len(cards)} cards -> total rows: {len(rows)}")
        time.sleep(polite_delay)

        # Next page
        if not go_to_next_page(driver):
            break
        page_num += 1

    driver.quit()
    df = pd.DataFrame(rows)
    return df


def main():
    parser = argparse.ArgumentParser(description="Scrape TripAdvisor reviews into CSV.")
    parser.add_argument("--url", required=True, help="TripAdvisor attraction URL")
    parser.add_argument("--track", required=True, choices=["city", "regional"], help="city or regional")
    parser.add_argument("--attraction", required=True, help="Attraction name (for the CSV)")
    parser.add_argument("--pages", type=int, default=3, help="Max pages to scrape")
    parser.add_argument("--out", default="data/nt_reviews.csv", help="Output CSV path (appends if exists)")
    parser.add_argument("--headful", action="store_true", help="Run with a visible browser (not headless)")
    args = parser.parse_args()

    df = scrape_tripadvisor(
        url=args.url,
        track=args.track,
        attraction_name=args.attraction,
        max_pages=args.pages,
        headless=not args.headful
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Append safely
    if out_path.exists():
        existing = pd.read_csv(out_path)
        combined = pd.concat([existing, df], ignore_index=True)
        combined.to_csv(out_path, index=False)
    else:
        df.to_csv(out_path, index=False)

    print(f"Saved {len(df)} new rows to {out_path.resolve()}")


if __name__ == "__main__":
    main()
