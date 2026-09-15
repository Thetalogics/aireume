"""
JD URL Scraper — extracts job description text from LinkedIn, Indeed, Naukri, and generic pages.
"""
import re

from app.backend.services.url_safety import safe_request_async

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


async def scrape_jd(url: str) -> str:
    """Fetch a job posting URL and return the extracted JD text."""
    if not _BS4_AVAILABLE:
        raise ImportError("beautifulsoup4 is required for URL extraction. Install it with: pip install beautifulsoup4 lxml")

    resp = await safe_request_async(
        "GET", url, timeout=20.0, headers=HEADERS, max_bytes=8 * 1024 * 1024
    )
    resp.raise_for_status()
    html = resp.text

    soup = BeautifulSoup(html, "lxml")  # noqa: F821

    # Remove script, style, nav, header, footer noise
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()

    # Platform-specific selectors
    text = ""

    if "linkedin.com" in url:
        # LinkedIn job details section
        for sel in [".description__text", ".job-description", ".jobs-description"]:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(separator="\n", strip=True)
                break

    elif "indeed.com" in url:
        for sel in ["#jobDescriptionText", ".jobsearch-jobDescriptionText"]:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(separator="\n", strip=True)
                break

    elif "naukri.com" in url:
        for sel in [".job-desc", ".jd-header", ".dang-inner-html"]:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(separator="\n", strip=True)
                break

    # Generic fallback: find the largest <div> or <section> by text content
    if not text or len(text) < 100:
        candidates = soup.find_all(["div", "section", "article"], recursive=True)
        best = max(candidates, key=lambda t: len(t.get_text()), default=None)
        if best:
            text = best.get_text(separator="\n", strip=True)

    # Clean up
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if len(text) < 50:
        raise ValueError("Could not extract meaningful job description from the URL")

    return text[:8000]  # cap at 8000 chars to avoid oversized JD
