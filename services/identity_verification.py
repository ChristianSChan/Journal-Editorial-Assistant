"""Reviewer contact retrieval from official personal or institutional pages."""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests

from services.reviewer_retrieval import ReviewerCandidate

SEARCH_URL = "https://duckduckgo.com/html/?q="
ORCID_EMPLOYMENTS_URL = "https://pub.orcid.org/v3.0/{orcid}/employments"
REQUEST_TIMEOUT_SECONDS = 8
MAX_CANDIDATES_TO_CHECK = 12
MAX_SEARCH_RESULTS = 12
MAX_PAGE_CHARS = 300_000
EMAIL_CONTEXT_CHARS = 320

EMAIL_PATTERN = re.compile(
    r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
    re.IGNORECASE,
)
PROFESSOR_PATTERN = re.compile(
    r"\b(assistant|associate|full|distinguished|emeritus|clinical|adjunct)?\s*professor\b|\bprof\.?\b",
    re.IGNORECASE,
)
DOCTOR_PATTERN = re.compile(r"\bdr\.?\s+[A-Z][A-Za-z-]+|\bph\.?d\.?\b", re.IGNORECASE)
SKIP_DOMAINS = (
    "google.",
    "scholar.google",
    "openalex.org",
    "semanticscholar.org",
    "doi.org",
    "crossref.org",
    "pubmed.ncbi.nlm.nih.gov",
    "orcid.org",
    "researchgate.net",
    "academia.edu",
    "linkedin.com",
    "x.com",
    "twitter.com",
    "facebook.com",
)
OFFICIAL_DOMAIN_HINTS = (
    ".edu",
    ".ac.",
    ".edu.",
    ".gov",
    ".org",
    "university",
    "college",
    "hospital",
    "institute",
    "centre",
    "center",
)


def attach_identity_verification(
    candidates: list[ReviewerCandidate],
) -> list[ReviewerCandidate]:
    """Verify reviewer title, profile URL, and email from official pages."""
    for candidate in candidates[:MAX_CANDIDATES_TO_CHECK]:
        retrieve_reviewer_contact(candidate)

    for candidate in candidates[MAX_CANDIDATES_TO_CHECK:]:
        candidate.title_status = "Not checked: contact lookup limit reached"
        candidate.contact_status = "Not checked: contact lookup limit reached"
        candidate.contact_confidence = "low"
        if not candidate.email:
            candidate.email_status = "Unavailable: contact lookup limit reached"

    return candidates


def retrieve_reviewer_contact(candidate: ReviewerCandidate) -> ReviewerCandidate:
    """Retrieve official profile URL and public email without guessing."""
    candidate.contact_confidence = "low"
    _enrich_from_orcid_employment(candidate)
    profile_url_found = False
    search_failed = ""
    for query in _contact_search_queries(candidate):
        try:
            search_html = _get_html(SEARCH_URL + quote_plus(query))
        except requests.RequestException as exc:
            search_failed = str(exc)
            continue

        for url in _extract_search_urls(search_html):
            if _should_skip_url(url) or not _looks_official(url, candidate):
                continue
            try:
                page_text = _get_text(url)
            except requests.RequestException:
                continue
            if not _page_mentions_candidate(page_text, candidate):
                continue

            profile_url_found = True
            candidate.official_profile_url = url
            candidate.identity_verification_url = url
            if not candidate.affiliation:
                candidate.affiliation = _infer_affiliation_from_page(page_text, url)
            _verify_title_from_page(candidate, page_text, url)
            _verify_email_from_page(candidate, page_text, url)
            _set_contact_confidence(candidate)

            if candidate.email:
                candidate.contact_status = "Official profile and associated email found"
                return candidate

    if profile_url_found:
        candidate.contact_status = "Official profile found; email not publicly listed"
        candidate.email = None
        candidate.email_status = "Unavailable: no associated email on official profile"
        _set_contact_confidence(candidate)
        return candidate

    if search_failed:
        candidate.title_status = f"Unavailable: web search failed ({search_failed})"
        candidate.contact_status = f"Unavailable: web search failed ({search_failed})"
        if not candidate.email:
            candidate.email_status = f"Unavailable: web search failed ({search_failed})"
        return candidate

    if not candidate.verified_title:
        candidate.title_status = "Unavailable: no official profile evidence found"
    candidate.email = None
    candidate.email_status = "Unavailable: no official profile email found"
    candidate.contact_status = "No official profile found"
    candidate.contact_confidence = "low"
    return candidate


def _contact_search_queries(candidate: ReviewerCandidate) -> list[str]:
    paper_titles = [paper.paper_title for paper in candidate.matching_papers[:2] if paper.paper_title]
    queries = [
        f'"{candidate.name}" "{candidate.affiliation or ""}" email profile',
        f'"{candidate.name}" "{candidate.affiliation or ""}" faculty email',
        f'"{candidate.name}" university email',
        f'"{candidate.name}" institutional profile',
    ]
    for title in paper_titles:
        queries.append(f'"{candidate.name}" "{title[:80]}"')
    return [re.sub(r"\s+", " ", query).strip() for query in queries]


def salutation(candidate: ReviewerCandidate) -> str:
    """Return a cautious salutation for invitation openers."""
    last_name = _last_name(candidate.name)
    if candidate.verified_title:
        return f"Dear {candidate.verified_title} {last_name}"
    return f"Dear Dr. {last_name}"


def _set_contact_confidence(candidate: ReviewerCandidate) -> None:
    if candidate.official_profile_url and candidate.email:
        candidate.contact_confidence = "high"
    elif candidate.official_profile_url:
        candidate.contact_confidence = "medium"
    else:
        candidate.contact_confidence = "low"


def _get_html(url: str) -> str:
    response = requests.get(
        url,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"User-Agent": "journal-review-tool/0.1"},
    )
    response.raise_for_status()
    return response.text[:MAX_PAGE_CHARS]


def _get_text(url: str) -> str:
    text = _get_html(url)
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text))


def _extract_search_urls(search_html: str) -> list[str]:
    urls: list[str] = []
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', search_html, flags=re.IGNORECASE)
    direct_urls = re.findall(r'https?://[^\s"\'<>]+', search_html)

    for raw_url in [*hrefs, *direct_urls]:
        url = html.unescape(raw_url)
        if url.startswith("//"):
            url = "https:" + url
        url = unquote(url)

        parsed = urlparse(url)
        if parsed.netloc.endswith("duckduckgo.com") and "uddg" in parsed.query:
            url = unquote(parse_qs(parsed.query).get("uddg", [""])[0])

        if url.startswith("http") and url not in urls:
            urls.append(url)
        if len(urls) >= MAX_SEARCH_RESULTS:
            break
    return urls


def _should_skip_url(url: str) -> bool:
    netloc = urlparse(url).netloc.casefold()
    return any(domain in netloc for domain in SKIP_DOMAINS)


def _looks_official(url: str, candidate: ReviewerCandidate) -> bool:
    netloc = urlparse(url).netloc.casefold()
    url_text = url.casefold()
    affiliation = (candidate.affiliation or "").casefold()
    affiliation_tokens = [
        token
        for token in re.findall(r"[a-z]{4,}", affiliation)
        if token not in {"university", "college", "hospital", "institute", "school"}
    ]
    if any(hint in netloc or hint in url_text for hint in OFFICIAL_DOMAIN_HINTS):
        return True
    return any(token in netloc or token in url_text for token in affiliation_tokens[:4])


def _page_mentions_candidate(page_text: str, candidate: ReviewerCandidate) -> bool:
    text = page_text.casefold()
    parts = [part.casefold() for part in candidate.name.split() if len(part) > 1]
    if len(parts) < 2:
        return candidate.name.casefold() in text
    return parts[0] in text and parts[-1] in text


def _verify_title_from_page(candidate: ReviewerCandidate, page_text: str, url: str) -> None:
    position_title = _extract_position_title(page_text)
    if position_title and not candidate.position_title:
        candidate.position_title = position_title
        candidate.position_title_status = f"Verified from official profile: {url}"
    if PROFESSOR_PATTERN.search(page_text):
        candidate.verified_title = "Prof."
        candidate.title_status = f"Verified from official profile: {url}"
        return
    if DOCTOR_PATTERN.search(page_text):
        candidate.verified_title = "Dr."
        candidate.title_status = f"Verified from official profile: {url}"


def _infer_affiliation_from_page(page_text: str, url: str) -> str | None:
    host_tokens = [
        token.capitalize()
        for token in re.findall(r"[a-z]{4,}", urlparse(url).netloc.casefold())
        if token not in {"www", "profile", "people", "staff", "faculty"}
    ]
    patterns = [
        r"\b([A-Z][A-Za-z&,\- ]{2,80}(?:University|College|Hospital|Institute|Center|Centre|School))\b",
        r"\b((?:University|College|Hospital|Institute|Center|Centre|School) of [A-Z][A-Za-z&,\- ]{2,80})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, page_text)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" ,")
    return " ".join(host_tokens[:3]) if host_tokens else None


def _enrich_from_orcid_employment(candidate: ReviewerCandidate) -> None:
    if not candidate.orcid:
        return
    try:
        response = requests.get(
            ORCID_EMPLOYMENTS_URL.format(orcid=candidate.orcid),
            headers={"Accept": "application/json"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return

    summaries = _orcid_employment_summaries(payload)
    for summary in summaries:
        role_title = summary.get("role-title")
        organization = summary.get("organization") or {}
        organization_name = organization.get("name")
        if role_title and not candidate.position_title:
            candidate.position_title = _clean_title(role_title)
            candidate.position_title_status = "From public ORCID employment record"
            if not candidate.verified_title and PROFESSOR_PATTERN.search(role_title):
                candidate.verified_title = "Prof."
                candidate.title_status = "Inferred from public ORCID employment title"
        if organization_name and not candidate.affiliation:
            candidate.affiliation = organization_name
        if candidate.position_title and candidate.affiliation:
            return


def _orcid_employment_summaries(payload: dict) -> list[dict]:
    summaries: list[dict] = []
    for group in payload.get("affiliation-group", []) or []:
        for item in group.get("summaries", []) or []:
            summary = item.get("employment-summary")
            if isinstance(summary, dict):
                summaries.append(summary)
    return summaries


def _extract_position_title(page_text: str) -> str | None:
    patterns = [
        r"\b(?:assistant|associate|full|distinguished|emeritus|clinical|adjunct)?\s*professor\b",
        r"\b(?:senior\s+)?lecturer\b",
        r"\breader\b",
        r"\bprincipal investigator\b",
        r"\bresearch scientist\b",
        r"\bscientist\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, page_text, flags=re.IGNORECASE)
        if match:
            return _clean_title(match.group(0))
    return None


def _clean_title(value: str) -> str:
    return " ".join(part.capitalize() for part in re.sub(r"\s+", " ", value).strip().split())


def _verify_email_from_page(candidate: ReviewerCandidate, page_text: str, url: str) -> None:
    emails = EMAIL_PATTERN.findall(page_text)
    if not emails:
        candidate.email = None
        return

    associated_email = _associated_email(candidate, page_text, emails)
    if associated_email:
        candidate.email = associated_email
        candidate.email_status = f"Verified from official profile: {url}"
        return

    candidate.email = None
    candidate.email_status = "Unavailable: email found but not clearly associated with reviewer"


def _associated_email(
    candidate: ReviewerCandidate,
    page_text: str,
    emails: list[str],
) -> str | None:
    text_lower = page_text.casefold()
    name_parts = [part.casefold() for part in candidate.name.split() if len(part) > 1]
    first = name_parts[0] if name_parts else ""
    last = name_parts[-1] if name_parts else ""

    for email in emails:
        email_lower = email.casefold()
        email_index = text_lower.find(email_lower)
        if email_index == -1:
            continue
        start = max(0, email_index - EMAIL_CONTEXT_CHARS)
        end = min(len(text_lower), email_index + EMAIL_CONTEXT_CHARS)
        context = text_lower[start:end]
        local_part = email_lower.split("@", 1)[0]

        name_in_context = first in context and last in context
        email_contains_name = bool(last and last.replace("-", "") in local_part.replace("-", ""))
        if name_in_context or email_contains_name:
            return email

    return None


def _last_name(name: str) -> str:
    parts = [part.strip() for part in name.split() if part.strip()]
    if not parts:
        return name
    return parts[-1]
