"""Approximate citation metric enrichment for reviewer candidates."""

from __future__ import annotations

import os

import requests

from services.reviewer_retrieval import ReviewerCandidate

OPENALEX_AUTHORS_URL = "https://api.openalex.org/authors"
SCOPUS_AUTHOR_URL = "https://api.elsevier.com/content/author/author_id"
SEMANTIC_SCHOLAR_AUTHOR_SEARCH_URL = (
    "https://api.semanticscholar.org/graph/v1/author/search"
)
SEMANTIC_SCHOLAR_AUTHOR_URL = "https://api.semanticscholar.org/graph/v1/author"
REQUEST_TIMEOUT_SECONDS = 15
MAX_SEMANTIC_SCHOLAR_LOOKUPS = 20


def attach_citation_metrics(
    candidates: list[ReviewerCandidate],
) -> list[ReviewerCandidate]:
    """Attach approximate citation and activity metrics without failing search."""
    for candidate in candidates:
        _attach_matched_paper_metrics(candidate)
        _attach_openalex_author_metrics(candidate)
        _attach_scopus_author_metrics(candidate)

    for candidate in candidates[:MAX_SEMANTIC_SCHOLAR_LOOKUPS]:
        _attach_semantic_scholar_metrics(candidate)

    for candidate in candidates[MAX_SEMANTIC_SCHOLAR_LOOKUPS:]:
        if candidate.h_index is None:
            candidate.h_index_source = "Unavailable: Semantic Scholar lookup limit reached"
        _refresh_status(candidate)

    return candidates


def _attach_matched_paper_metrics(candidate: ReviewerCandidate) -> None:
    citation_counts = [
        paper.citation_count
        for paper in candidate.matching_papers
        if paper.citation_count is not None
    ]
    years = [
        paper.publication_year
        for paper in candidate.matching_papers
        if paper.publication_year is not None
    ]

    if citation_counts:
        candidate.matched_paper_citation_count = sum(citation_counts)
        candidate.matched_paper_citation_source = "Approximate, from normalized source evidence"
    else:
        candidate.matched_paper_citation_source = "Unavailable in displayed source evidence"

    candidate.recent_activity_year = max(years) if years else None
    candidate.publication_count = len(candidate.matching_papers)
    candidate.publication_count_source = "Displayed matching publications only"
    _refresh_status(candidate)


def _attach_openalex_author_metrics(candidate: ReviewerCandidate) -> None:
    if not candidate.source_openalex_author_id:
        candidate.total_citation_source = "Unavailable: no OpenAlex author id"
        _refresh_status(candidate)
        return

    try:
        response = requests.get(
            _openalex_author_api_url(candidate.source_openalex_author_id),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        candidate.total_citation_source = f"Unavailable: OpenAlex author lookup failed ({exc})"
        _refresh_status(candidate)
        return
    cited_by_count = payload.get("cited_by_count")
    if isinstance(cited_by_count, int):
        candidate.total_citation_count = cited_by_count
        candidate.total_citation_source = "Approximate, from OpenAlex author cited_by_count"
    else:
        candidate.total_citation_source = "Unavailable in OpenAlex author metadata"

    works_count = payload.get("works_count")
    if isinstance(works_count, int):
        candidate.publication_count = works_count
        candidate.publication_count_source = "Approximate, from OpenAlex author works_count"

    h_index = (payload.get("summary_stats") or {}).get("h_index")
    if isinstance(h_index, int) and candidate.h_index is None:
        candidate.h_index = h_index
        candidate.h_index_source = "Approximate, from OpenAlex author summary_stats.h_index"

    if not candidate.orcid:
        candidate.orcid = _normalize_orcid(payload.get("orcid"))
    if not candidate.affiliation:
        candidate.affiliation = _openalex_affiliation(payload)

    _refresh_status(candidate)


def _attach_scopus_author_metrics(candidate: ReviewerCandidate) -> None:
    api_key = os.getenv("SCOPUS_API_KEY", "").strip()
    if not api_key:
        return
    if not candidate.scopus_author_id:
        if candidate.total_citation_count is None:
            candidate.total_citation_source = "Unavailable: Scopus did not return an author ID for this candidate"
        if candidate.h_index is None:
            candidate.h_index_source = "Unavailable: Scopus did not return an author ID for this candidate"
        _refresh_status(candidate)
        return

    try:
        response = requests.get(
            f"{SCOPUS_AUTHOR_URL}/{candidate.scopus_author_id}",
            params={"view": "ENHANCED"},
            headers={"X-ELS-APIKey": api_key, "Accept": "application/json"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        if candidate.total_citation_count is None:
            candidate.total_citation_source = f"Unavailable: Scopus author lookup failed ({exc})"
        _refresh_status(candidate)
        return

    author_record = _first_scopus_author_record(payload)
    if not author_record:
        if candidate.total_citation_count is None:
            candidate.total_citation_source = "Unavailable: Scopus author profile was empty"
        if candidate.h_index is None:
            candidate.h_index_source = "Unavailable: Scopus author profile was empty"
        _refresh_status(candidate)
        return

    cited_by_count = _int_or_none(
        (author_record.get("coredata") or {}).get("cited-by-count")
    )
    document_count = _int_or_none(
        (author_record.get("coredata") or {}).get("document-count")
    )
    if document_count is None:
        document_count = _int_or_none(author_record.get("document-count"))
    if document_count is not None:
        candidate.publication_count = document_count
        candidate.publication_count_source = "Approximate, from Scopus author profile"

    if cited_by_count is not None:
        candidate.total_citation_count = cited_by_count
        candidate.total_citation_source = "Approximate, from Scopus author profile"
    elif candidate.total_citation_count is None:
        candidate.total_citation_source = "Unavailable in Scopus author profile"

    h_index = _int_or_none(author_record.get("h-index"))
    if h_index is not None:
        candidate.h_index = h_index
        candidate.h_index_source = "Approximate, from Scopus author profile"
    elif candidate.h_index is None:
        candidate.h_index_source = "Unavailable in Scopus author profile"

    preferred_name = author_record.get("preferred-name") or {}
    if not candidate.affiliation:
        candidate.affiliation = _scopus_affiliation(author_record)
    if not candidate.position_title:
        indexed_name = preferred_name.get("indexed-name")
        if indexed_name:
            candidate.position_title_status = "Unavailable in Scopus author profile"

    _refresh_status(candidate)


def _attach_semantic_scholar_metrics(candidate: ReviewerCandidate) -> None:
    if candidate.semantic_scholar_author_id:
        author_match = _fetch_semantic_scholar_author_by_id(candidate)
        if author_match is not None:
            _apply_semantic_scholar_author_metrics(
                candidate,
                author_match,
                "Semantic Scholar author ID",
            )
            _refresh_status(candidate)
            return

    params = {
        "query": _semantic_scholar_query(candidate),
        "limit": 5,
        "fields": "name,url,paperCount,citationCount,hIndex,affiliations",
    }
    headers = _semantic_scholar_headers()

    try:
        response = requests.get(
            SEMANTIC_SCHOLAR_AUTHOR_SEARCH_URL,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        candidate.h_index_source = f"Unavailable: Semantic Scholar lookup failed ({exc})"
        _refresh_status(candidate)
        return

    matches = payload.get("data", [])
    if not isinstance(matches, list) or not matches:
        candidate.h_index_source = "Unavailable: no Semantic Scholar author match"
        _refresh_status(candidate)
        return

    best_match = _best_author_match(candidate, matches)
    if best_match is None:
        candidate.h_index_source = "Unavailable: no confident Semantic Scholar author match"
        _refresh_status(candidate)
        return

    _apply_semantic_scholar_author_metrics(
        candidate,
        best_match,
        "Semantic Scholar author search",
    )
    _refresh_status(candidate)


def _fetch_semantic_scholar_author_by_id(candidate: ReviewerCandidate) -> dict | None:
    headers = _semantic_scholar_headers()
    try:
        response = requests.get(
            f"{SEMANTIC_SCHOLAR_AUTHOR_URL}/{candidate.semantic_scholar_author_id}",
            params={"fields": "name,url,paperCount,citationCount,hIndex,affiliations"},
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        candidate.h_index_source = f"Unavailable: Semantic Scholar author lookup failed ({exc})"
        _refresh_status(candidate)
        return None
    return payload if isinstance(payload, dict) else None


def _apply_semantic_scholar_author_metrics(
    candidate: ReviewerCandidate,
    author_payload: dict,
    source_label: str,
) -> None:
    h_index = author_payload.get("hIndex")
    if isinstance(h_index, int):
        candidate.h_index = h_index
        candidate.h_index_source = f"Approximate, from {source_label}"
    else:
        candidate.h_index_source = "Unavailable in Semantic Scholar author match"

    citation_count = author_payload.get("citationCount")
    if candidate.total_citation_count is None and isinstance(citation_count, int):
        candidate.total_citation_count = citation_count
        candidate.total_citation_source = f"Approximate, from {source_label}"

    paper_count = author_payload.get("paperCount")
    if isinstance(paper_count, int) and candidate.publication_count is None:
        candidate.publication_count = paper_count
        candidate.publication_count_source = f"Approximate, from {source_label}"

    if not candidate.affiliation:
        affiliations = author_payload.get("affiliations")
        if isinstance(affiliations, list) and affiliations:
            candidate.affiliation = str(affiliations[0])


def _openalex_author_api_url(author_id: str) -> str:
    openalex_prefix = "https://openalex.org/"
    if author_id.startswith(openalex_prefix):
        return OPENALEX_AUTHORS_URL + "/" + author_id.removeprefix(openalex_prefix)
    return author_id


def _semantic_scholar_query(candidate: ReviewerCandidate) -> str:
    if candidate.affiliation:
        return f"{candidate.name} {candidate.affiliation}"
    return candidate.name


def _semantic_scholar_headers() -> dict[str, str]:
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    if not api_key:
        return {}
    return {"x-api-key": api_key}


def _best_author_match(
    candidate: ReviewerCandidate,
    matches: list[dict],
) -> dict | None:
    candidate_name = candidate.name.casefold().strip()
    for match in matches:
        match_name = str(match.get("name", "")).casefold().strip()
        if match_name == candidate_name:
            return match
    return None


def _first_scopus_author_record(payload: dict) -> dict | None:
    records = payload.get("author-retrieval-response")
    if isinstance(records, list) and records:
        return records[0] if isinstance(records[0], dict) else None
    if isinstance(records, dict):
        return records
    return None


def _openalex_affiliation(payload: dict) -> str | None:
    institutions = payload.get("last_known_institutions") or []
    names = [
        institution.get("display_name")
        for institution in institutions
        if isinstance(institution, dict) and institution.get("display_name")
    ]
    if names:
        return ", ".join(names)

    affiliations = payload.get("affiliations") or []
    for affiliation in affiliations if isinstance(affiliations, list) else []:
        institution = affiliation.get("institution") or {}
        if institution.get("display_name"):
            return institution.get("display_name")
    return None


def _scopus_affiliation(author_record: dict) -> str | None:
    affiliation_current = author_record.get("affiliation-current") or {}
    affiliation = affiliation_current.get("affiliation") or {}
    if isinstance(affiliation, list):
        affiliation = affiliation[0] if affiliation else {}
    if isinstance(affiliation, dict):
        name = affiliation.get("ip-doc", {}).get("afdispname") or affiliation.get("affiliation-name")
        city = affiliation.get("city")
        country = affiliation.get("country")
        parts = [part for part in [name, city, country] if part]
        if parts:
            return ", ".join(parts)
    return None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _normalize_orcid(orcid: object) -> str | None:
    if not isinstance(orcid, str) or not orcid.strip():
        return None
    return orcid.replace("https://orcid.org/", "").strip()


def _refresh_status(candidate: ReviewerCandidate) -> None:
    statuses = [
        candidate.total_citation_source,
        candidate.h_index_source,
        candidate.matched_paper_citation_source,
    ]
    available = [status for status in statuses if status.startswith("Approximate")]
    if available:
        candidate.citation_metrics_status = "Some approximate citation metrics available"
    else:
        candidate.citation_metrics_status = "Citation metrics unavailable"
