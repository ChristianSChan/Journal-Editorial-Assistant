"""Approximate citation metric enrichment for reviewer candidates."""

from __future__ import annotations

import os

import requests

from services.openalex_config import openalex_headers, openalex_params
from services.reviewer_retrieval import ReviewerCandidate

OPENALEX_AUTHORS_URL = "https://api.openalex.org/authors"
SCOPUS_AUTHOR_URL = "https://api.elsevier.com/content/author/author_id"
SEMANTIC_SCHOLAR_AUTHOR_SEARCH_URL = (
    "https://api.semanticscholar.org/graph/v1/author/search"
)
SEMANTIC_SCHOLAR_AUTHOR_URL = "https://api.semanticscholar.org/graph/v1/author"
SEMANTIC_SCHOLAR_AUTHOR_BATCH_URL = (
    "https://api.semanticscholar.org/graph/v1/author/batch"
)
REQUEST_TIMEOUT_SECONDS = 15
MAX_SEMANTIC_SCHOLAR_LOOKUPS = 20
SEMANTIC_SCHOLAR_AUTHOR_FIELDS = (
    "authorId,name,aliases,url,affiliations,homepage,paperCount,"
    "citationCount,hIndex,externalIds"
)
SEMANTIC_SCHOLAR_AUTHOR_BASIC_FIELDS = (
    "authorId,name,url,affiliations,paperCount,citationCount,hIndex"
)


def attach_citation_metrics(
    candidates: list[ReviewerCandidate],
) -> list[ReviewerCandidate]:
    """Attach approximate citation and activity metrics without failing search."""
    for candidate in candidates:
        _attach_matched_paper_metrics(candidate)
        _attach_openalex_author_metrics(candidate)
        _attach_scopus_author_metrics(candidate)

    semantic_candidates = candidates[:MAX_SEMANTIC_SCHOLAR_LOOKUPS]
    _attach_semantic_scholar_metrics_by_id_batch(semantic_candidates)

    for candidate in semantic_candidates:
        if _has_semantic_scholar_author_enrichment(candidate):
            _refresh_status(candidate)
            continue
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
            params=openalex_params(),
            headers=openalex_headers(),
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
        "fields": SEMANTIC_SCHOLAR_AUTHOR_FIELDS,
    }
    headers = _semantic_scholar_headers()

    try:
        response = _semantic_scholar_get_with_field_fallback(
            SEMANTIC_SCHOLAR_AUTHOR_SEARCH_URL,
            params,
            headers,
        )
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


def _attach_semantic_scholar_metrics_by_id_batch(
    candidates: list[ReviewerCandidate],
) -> None:
    candidates_by_id = {
        str(candidate.semantic_scholar_author_id): candidate
        for candidate in candidates
        if candidate.semantic_scholar_author_id
    }
    if not candidates_by_id:
        return

    headers = _semantic_scholar_headers() | {"Content-Type": "application/json"}
    params = {"fields": SEMANTIC_SCHOLAR_AUTHOR_FIELDS}
    try:
        response = requests.post(
            SEMANTIC_SCHOLAR_AUTHOR_BATCH_URL,
            params=params,
            json={"ids": list(candidates_by_id.keys())},
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code == 400:
            response = requests.post(
                SEMANTIC_SCHOLAR_AUTHOR_BATCH_URL,
                params={"fields": SEMANTIC_SCHOLAR_AUTHOR_BASIC_FIELDS},
                json={"ids": list(candidates_by_id.keys())},
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        for candidate in candidates_by_id.values():
            if candidate.h_index is None:
                candidate.h_index_source = (
                    f"Unavailable: Semantic Scholar batch lookup failed ({exc})"
                )
            _refresh_status(candidate)
        return

    if not isinstance(payload, list):
        return

    for author_payload in payload:
        if not isinstance(author_payload, dict):
            continue
        author_id = str(author_payload.get("authorId") or "")
        candidate = candidates_by_id.get(author_id)
        if candidate is None:
            continue
        _apply_semantic_scholar_author_metrics(
            candidate,
            author_payload,
            "Semantic Scholar author batch",
        )
        _refresh_status(candidate)


def _fetch_semantic_scholar_author_by_id(candidate: ReviewerCandidate) -> dict | None:
    headers = _semantic_scholar_headers()
    try:
        response = _semantic_scholar_get_with_field_fallback(
            f"{SEMANTIC_SCHOLAR_AUTHOR_URL}/{candidate.semantic_scholar_author_id}",
            {"fields": SEMANTIC_SCHOLAR_AUTHOR_FIELDS},
            headers,
        )
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        candidate.h_index_source = f"Unavailable: Semantic Scholar author lookup failed ({exc})"
        _refresh_status(candidate)
        return None
    return payload if isinstance(payload, dict) else None


def _semantic_scholar_get_with_field_fallback(
    url: str,
    params: dict[str, object],
    headers: dict[str, str],
) -> requests.Response:
    response = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code == 400 and params.get("fields") == SEMANTIC_SCHOLAR_AUTHOR_FIELDS:
        fallback_params = params | {"fields": SEMANTIC_SCHOLAR_AUTHOR_BASIC_FIELDS}
        response = requests.get(
            url,
            params=fallback_params,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    response.raise_for_status()
    return response


def _apply_semantic_scholar_author_metrics(
    candidate: ReviewerCandidate,
    author_payload: dict,
    source_label: str,
) -> None:
    _apply_semantic_scholar_author_identity(candidate, author_payload)

    h_index = author_payload.get("hIndex")
    if isinstance(h_index, int):
        candidate.h_index = h_index
        candidate.h_index_source = f"Approximate, from {source_label}"
    elif candidate.h_index is None:
        candidate.h_index_source = "Unavailable in Semantic Scholar author match"

    citation_count = author_payload.get("citationCount")
    if candidate.total_citation_count is None and isinstance(citation_count, int):
        candidate.total_citation_count = citation_count
        candidate.total_citation_source = f"Approximate, from {source_label}"

    paper_count = author_payload.get("paperCount")
    if isinstance(paper_count, int) and (
        candidate.publication_count is None
        or candidate.publication_count_source == "Displayed matching publications only"
    ):
        candidate.publication_count = paper_count
        candidate.publication_count_source = f"Approximate, from {source_label}"

    if not candidate.affiliation:
        affiliations = author_payload.get("affiliations")
        if isinstance(affiliations, list) and affiliations:
            candidate.affiliation = str(affiliations[0])


def _apply_semantic_scholar_author_identity(
    candidate: ReviewerCandidate,
    author_payload: dict,
) -> None:
    _ensure_semantic_scholar_profile_fields(candidate)

    author_id = author_payload.get("authorId") or candidate.semantic_scholar_author_id
    if author_id:
        candidate.semantic_scholar_author_id = str(author_id)
        _add_source_id(candidate, "Semantic Scholar author", str(author_id))
        candidate.source_coverage["Semantic Scholar"] = True

    profile_url = author_payload.get("url")
    if isinstance(profile_url, str) and profile_url.strip():
        candidate.profile_urls["Semantic Scholar"] = profile_url.strip()
        candidate.identity_verification_url = (
            candidate.identity_verification_url or profile_url.strip()
        )

    homepage = author_payload.get("homepage")
    if isinstance(homepage, str) and homepage.strip():
        candidate.profile_urls["Homepage"] = homepage.strip()
        candidate.official_profile_url = candidate.official_profile_url or homepage.strip()

    aliases = author_payload.get("aliases")
    if isinstance(aliases, list):
        for alias in aliases:
            if isinstance(alias, str) and alias and alias not in candidate.known_aliases:
                candidate.known_aliases.append(alias)

    affiliations = author_payload.get("affiliations")
    if isinstance(affiliations, list):
        for affiliation in affiliations:
            if (
                isinstance(affiliation, str)
                and affiliation
                and affiliation not in candidate.known_affiliations
            ):
                candidate.known_affiliations.append(affiliation)

    external_ids = author_payload.get("externalIds")
    if isinstance(external_ids, dict):
        for source, value in external_ids.items():
            if isinstance(value, str):
                _add_source_id(candidate, source, value)
                if source.casefold() == "orcid" and not candidate.orcid:
                    candidate.orcid = _normalize_orcid(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        _add_source_id(candidate, source, item)
                        if source.casefold() == "orcid" and not candidate.orcid:
                            candidate.orcid = _normalize_orcid(item)


def _add_source_id(candidate: ReviewerCandidate, source: str, value: str | None) -> None:
    if not value:
        return
    ids = candidate.source_ids.setdefault(source, [])
    if value not in ids:
        ids.append(value)


def _has_semantic_scholar_author_enrichment(candidate: ReviewerCandidate) -> bool:
    _ensure_semantic_scholar_profile_fields(candidate)
    return (
        candidate.h_index_source.startswith("Approximate, from Semantic Scholar")
        or candidate.total_citation_source.startswith("Approximate, from Semantic Scholar")
        or candidate.publication_count_source.startswith("Approximate, from Semantic Scholar")
        or "Semantic Scholar" in candidate.profile_urls
    )


def _ensure_semantic_scholar_profile_fields(candidate: ReviewerCandidate) -> None:
    if not hasattr(candidate, "profile_urls"):
        candidate.profile_urls = {}
    if not hasattr(candidate, "known_aliases"):
        candidate.known_aliases = []
    if not hasattr(candidate, "known_affiliations"):
        candidate.known_affiliations = []


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
