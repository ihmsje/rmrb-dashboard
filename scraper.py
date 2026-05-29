"""Scraper for People's Daily (人民日报) — full daily edition, all pages."""

import json
import os
import re
import time
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://paper.people.com.cn/rmrb/pc"
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def get_date_range(days: int = 7) -> list[str]:
    """Return list of date strings (YYYY-MM-DD) for the last N days."""
    today = datetime.now()
    return [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]


def _date_to_url_parts(date_str: str) -> tuple[str, str]:
    """Convert 'YYYY-MM-DD' to ('YYYYMM', 'DD') for URL construction."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.strftime("%Y%m"), dt.strftime("%d")


def cache_file(date_str: str) -> str:
    """Path to the per-date cache file."""
    return os.path.join(DATA_DIR, f"edition_{date_str}.json")


def discover_pages(date_str: str) -> list[int]:
    """Return sorted page numbers for a date's edition (from the page-1 index)."""
    ym, dd = _date_to_url_parts(date_str)
    index_url = f"{BASE_URL}/layout/{ym}/{dd}/node_01.html"
    try:
        resp = requests.get(index_url, headers=HEADERS, timeout=15)
        resp.encoding = "utf-8"
        if resp.status_code != 200:
            return []
    except requests.RequestException as e:
        print(f"[WARN] Could not load page index for {date_str}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    pages = set()
    for a in soup.find_all("a", href=True):
        m = re.search(r"node_(\d+)\.html$", a["href"])
        # Only same-edition links (skip the /pad/ mirror)
        if m and "/pad/" not in a["href"]:
            pages.add(int(m.group(1)))
    return sorted(pages) or [1]


def fetch_article_links(date_str: str, page: int) -> list[dict]:
    """Fetch article links listed on a single page (layout node) for a date."""
    ym, dd = _date_to_url_parts(date_str)
    listing_url = f"{BASE_URL}/layout/{ym}/{dd}/node_{page:02d}.html"
    try:
        resp = requests.get(listing_url, headers=HEADERS, timeout=15)
        resp.encoding = "utf-8"
        if resp.status_code != 200:
            return []
    except requests.RequestException as e:
        print(f"[WARN] Request error for {date_str} p{page}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    articles = []
    seen = set()
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if "content_" in href and href.endswith(".html"):
            title = a_tag.get_text(strip=True)
            if not title:
                continue
            if href.startswith("http"):
                article_url = href
            else:
                article_url = f"{BASE_URL}/content/{ym}/{dd}/{href.split('/')[-1]}"
            if article_url in seen:
                continue
            seen.add(article_url)
            articles.append({
                "title": title,
                "url": article_url,
                "date": date_str,
                "page": page,
            })
    return articles


def fetch_article_content(article: dict) -> dict:
    """Fetch full text for a single article."""
    try:
        resp = requests.get(article["url"], headers=HEADERS, timeout=15)
        resp.encoding = "utf-8"
        if resp.status_code != 200:
            article["content"] = ""
            return article
    except requests.RequestException:
        article["content"] = ""
        return article

    soup = BeautifulSoup(resp.text, "html.parser")
    content_div = soup.find(id="ozoom") or soup.find(class_="article") or soup.find("article")
    if content_div:
        for tag in content_div.find_all(["script", "style"]):
            tag.decompose()
        text = content_div.get_text(separator="\n", strip=True)
    else:
        paragraphs = soup.find_all("p")
        text = "\n".join(p.get_text(strip=True) for p in paragraphs)

    article["content"] = text
    return article


def scrape_date(date_str: str, delay: float = 0.2) -> list[dict]:
    """Scrape every article across every page of a date's edition."""
    pages = discover_pages(date_str)
    print(f"Scraping {date_str}: {len(pages)} pages")
    articles = []
    seen = set()
    for page in pages:
        links = fetch_article_links(date_str, page)
        for link in links:
            if link["url"] in seen:
                continue
            seen.add(link["url"])
            articles.append(fetch_article_content(link))
            time.sleep(delay)
    print(f"  {date_str}: {len(articles)} articles")
    return articles


def load_edition(date_str: str) -> list[dict]:
    """Load a cached edition for a date, or [] if not cached."""
    path = cache_file(date_str)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_edition(date_str: str, articles: list[dict]) -> None:
    """Save a date's edition to its cache file."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(cache_file(date_str), "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)


def get_edition(date_str: str, refresh: bool = False) -> list[dict]:
    """Return a date's edition, scraping (and caching) if needed."""
    if not refresh:
        cached = load_edition(date_str)
        if cached:
            return cached
    articles = scrape_date(date_str)
    if articles:
        save_edition(date_str, articles)
    return articles


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        dates = sys.argv[1:]
    else:
        dates = get_date_range(7)
    for d in dates:
        get_edition(d, refresh=True)
