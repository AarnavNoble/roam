"""
Data collection from Wikivoyage and Reddit for RAG pipeline.
Outputs raw text files per destination to data/raw/.
"""

import os
import json
import time
import httpx
from pathlib import Path

RAW_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "raw"
RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {"User-Agent": "roam-travel-app/1.0 (https://github.com/AarnavNoble/roam)"}


def fetch_wikivoyage(destination: str) -> str:
    """Fetch travel guide text from Wikivoyage for a destination."""
    url = "https://en.wikivoyage.org/w/api.php"
    params = {
        "action": "query",
        "titles": destination,
        "prop": "extracts",
        "explaintext": True,
        "format": "json",
    }
    resp = httpx.get(url, params=params, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    pages = resp.json().get("query", {}).get("pages", {})
    for page in pages.values():
        return page.get("extract", "")
    return ""


def fetch_reddit_posts(destination: str, limit: int = 25) -> list[str]:
    """Fetch top Reddit posts about a destination using the public JSON API."""
    subreddits = ["travel", "solotravel"]
    posts = []
    headers = {"User-Agent": "roam-scraper/1.0"}

    for sub in subreddits:
        url = f"https://www.reddit.com/r/{sub}/search.json"
        params = {"q": destination, "sort": "top", "t": "year", "limit": limit}
        try:
            resp = httpx.get(url, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            items = resp.json().get("data", {}).get("children", [])
            for item in items:
                data = item.get("data", {})
                title = data.get("title", "")
                selftext = data.get("selftext", "")
                if selftext and len(selftext) > 100:
                    posts.append(f"{title}\n{selftext}")
        except Exception as e:
            print(f"Reddit fetch failed for r/{sub}: {e}")
        time.sleep(1)  # be polite to Reddit

    return posts


def scrape_destination(destination: str) -> str:
    """Scrape all sources for a destination and return combined text."""
    print(f"Scraping: {destination}")
    parts = []

    wiki = fetch_wikivoyage(destination)
    if wiki:
        parts.append(f"=== Wikivoyage: {destination} ===\n{wiki}")

    reddit_posts = fetch_reddit_posts(destination)
    if reddit_posts:
        parts.append(f"=== Reddit posts: {destination} ===\n" + "\n\n".join(reddit_posts))

    return "\n\n".join(parts)


def scrape_and_save(destination: str) -> Path:
    """Scrape destination and save to data/raw/<destination>.txt"""
    text = scrape_destination(destination)
    out_path = RAW_DATA_DIR / f"{destination.lower().replace(' ', '_')}.txt"
    out_path.write_text(text, encoding="utf-8")
    print(f"Saved {len(text)} chars to {out_path}")
    return out_path


if __name__ == "__main__":
    scrape_and_save("Tokyo")
