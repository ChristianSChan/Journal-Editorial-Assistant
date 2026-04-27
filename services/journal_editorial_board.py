"""Editorial board lookup for prioritizing journal-board candidates."""

from __future__ import annotations

import html
import re
from functools import lru_cache

import requests

from services.reviewer_retrieval import ReviewerCandidate

REQUEST_TIMEOUT_SECONDS = 8
MAX_BOARD_PAGES = 3
MAX_PAGE_CHARS = 500_000

BOARD_TERMS = ("editorial board", "editors", "associate editors", "editor in chief")


def _normalized_journal_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold())

SUPPORTED_JOURNAL_BOARD_URLS: dict[str, tuple[str, ...]] = {}

SUPPORTED_JOURNALS = tuple(SUPPORTED_JOURNAL_BOARD_URLS)
_SUPPORTED_JOURNAL_LOOKUP = {
    _normalized_journal_name(name): name for name in SUPPORTED_JOURNALS
}

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36 journal-review-tool/0.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_URL_NOTES: dict[str, str] = {}

_FALLBACK_BOARD_TEXTS: dict[str, str] = {}

_FALLBACK_BOARD_SOURCES: dict[str, str] = {}


def supported_journals() -> tuple[str, ...]:
    return SUPPORTED_JOURNALS


def normalize_supported_journal_name(journal_name: str) -> str:
    return journal_name.strip()


def editorial_board_lookup_note(journal_name: str) -> str:
    supported_name = normalize_supported_journal_name(journal_name)
    if not supported_name:
        return "Editorial board lookup not checked: no journal name was entered."
    if supported_name not in SUPPORTED_JOURNAL_BOARD_URLS:
        return "Editorial board lookup not configured for this journal in the public template."
    return _URL_NOTES.get(supported_name, "Editorial board lookup configured for this journal.")


def mark_editorial_board_members(
    candidates: list[ReviewerCandidate],
    journal_name: str,
) -> list[ReviewerCandidate]:
    journal_name = normalize_supported_journal_name(journal_name)
    if not journal_name:
        for candidate in candidates:
            candidate.editorial_board_status = "Not checked: no journal name"
        return candidates

    board_pages = _editorial_board_pages(journal_name)
    if not board_pages:
        for candidate in candidates:
            candidate.editorial_board_status = (
                "No readable editorial board page found for this journal"
            )
        return candidates

    for candidate in candidates:
        normalized_name = _normalized_name(candidate.name)
        for url, page_text in board_pages:
            if normalized_name and normalized_name in _normalized_compact(page_text):
                candidate.is_editorial_board_member = True
                candidate.editorial_board_source = url
                candidate.editorial_board_status = "Name appears on possible editorial board page"
                break
        if not candidate.is_editorial_board_member:
            candidate.editorial_board_status = "Not found on checked editorial board pages"
    return candidates


@lru_cache(maxsize=32)
def _editorial_board_pages(journal_name: str) -> tuple[tuple[str, str], ...]:
    journal_name = normalize_supported_journal_name(journal_name)
    urls = SUPPORTED_JOURNAL_BOARD_URLS.get(journal_name, ())
    pages: list[tuple[str, str]] = []
    for url in urls:
        if len(pages) >= MAX_BOARD_PAGES:
            break
        try:
            page_text = _get_text(url)
        except requests.RequestException:
            continue
        if _looks_like_board_page(page_text, journal_name):
            pages.append((url, page_text))
    if not pages and journal_name in _FALLBACK_BOARD_TEXTS:
        pages.append((
            _FALLBACK_BOARD_SOURCES[journal_name],
            _FALLBACK_BOARD_TEXTS[journal_name],
        ))
    return tuple(pages)


def _looks_like_board_page(page_text: str, journal_name: str) -> bool:
    text = page_text.casefold()
    journal_tokens = [token for token in re.findall(r"[a-z]{4,}", journal_name.casefold())[:5]]
    has_journal = any(token in text for token in journal_tokens)
    has_board_terms = any(term in text for term in BOARD_TERMS)
    return has_board_terms and (has_journal or "journal" in text)


def _get_html(url: str) -> str:
    response = requests.get(
        url,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers=_FETCH_HEADERS,
    )
    response.raise_for_status()
    return response.text[:MAX_PAGE_CHARS]


def _get_text(url: str) -> str:
    text = _get_html(url)
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text))


def _normalized_name(name: str) -> str:
    parts = [part for part in re.findall(r"[a-z]+", name.casefold()) if len(part) > 1]
    return "".join(parts)


def _normalized_compact(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.casefold())
