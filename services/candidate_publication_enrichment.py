"""Follow-up publication enrichment for already identified reviewer candidates."""

from __future__ import annotations

import os
import re
from urllib.parse import quote

import requests

from services.llm_assist import build_reviewer_search_profile
from services.reviewer_retrieval import CandidateEvidence, ReviewerCandidate, ReviewerSearchInput, extract_search_terms

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
SCOPUS_SEARCH_URL = "https://api.elsevier.com/content/search/scopus"
REQUEST_TIMEOUT_SECONDS = 7
MAX_CANDIDATES_TO_ENRICH = 12
MAX_WORKS_PER_CANDIDATE = 5


def enrich_candidate_publications(
    candidates: list[ReviewerCandidate],
    search_input: ReviewerSearchInput,
) -> list[ReviewerCandidate]:
    """Fetch additional author-specific matching papers for top candidates."""
    profile = build_reviewer_search_profile(search_input)
    terms = _dedupe([
        *search_input.keywords,
        *profile.key_topics,
        *profile.methods,
        *profile.populations_or_contexts,
        *extract_search_terms(search_input),
    ])
    queries = profile.queries[:2]
    if not terms and not queries:
        return candidates

    for candidate in candidates[:MAX_CANDIDATES_TO_ENRICH]:
        if candidate.source_openalex_author_id:
            _add_evidence_items(candidate, _openalex_author_works(candidate, queries, terms))
        if candidate.scopus_author_id and os.getenv("SCOPUS_API_KEY", "").strip():
            _add_evidence_items(candidate, _scopus_author_works(candidate, queries, terms))
        _refresh_candidate_summary(candidate)
    return candidates


def _openalex_author_works(
    candidate: ReviewerCandidate,
    queries: list[str],
    terms: list[str],
) -> list[CandidateEvidence]:
    author_id = candidate.source_openalex_author_id or ""
    author_filter_id = author_id.removeprefix("https://openalex.org/")
    if not author_filter_id:
        return []

    evidence: list[CandidateEvidence] = []
    for query in queries:
        try:
            response = requests.get(
                OPENALEX_WORKS_URL,
                params={
                    "filter": f"authorships.author.id:{author_filter_id}",
                    "search": query,
                    "per-page": MAX_WORKS_PER_CANDIDATE,
                    "sort": "publication_year:desc",
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            works = response.json().get("results", [])
        except (requests.RequestException, ValueError):
            continue

        for work in works if isinstance(works, list) else []:
            title = work.get("display_name")
            work_id = work.get("id")
            if not title or not work_id:
                continue
            evidence.append(
                CandidateEvidence(
                    reviewer_name=candidate.name,
                    affiliation=candidate.affiliation,
                    source="OpenAlex",
                    paper_title=title,
                    abstract=_openalex_abstract(work),
                    journal_name=_openalex_journal_name(work),
                    publication_type=_normalize_publication_type(work.get("type")),
                    publication_language=work.get("language"),
                    publication_year=_int_or_none(work.get("publication_year")),
                    doi=_normalize_doi(work.get("doi")),
                    url=work_id,
                    citation_count=_int_or_none(work.get("cited_by_count")),
                    openalex_author_id=candidate.source_openalex_author_id,
                    openalex_work_id=work_id,
                    matched_keywords=_matched_terms(title, terms),
                    orcid=candidate.orcid,
                )
            )
    return evidence


def _scopus_author_works(
    candidate: ReviewerCandidate,
    queries: list[str],
    terms: list[str],
) -> list[CandidateEvidence]:
    api_key = os.getenv("SCOPUS_API_KEY", "").strip()
    if not api_key or not candidate.scopus_author_id:
        return []

    evidence: list[CandidateEvidence] = []
    for query in queries:
        try:
            response = requests.get(
                SCOPUS_SEARCH_URL,
                params={
                    "query": f"AUTHOR-ID({candidate.scopus_author_id}) AND TITLE-ABS-KEY({_scopus_query(query)})",
                    "count": MAX_WORKS_PER_CANDIDATE,
                    "sort": "-coverDate",
                    "field": "dc:title,prism:publicationName,prism:coverDate,prism:doi,citedby-count,eid,prism:url",
                },
                headers={"X-ELS-APIKey": api_key, "Accept": "application/json"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            entries = response.json().get("search-results", {}).get("entry", [])
        except (requests.RequestException, ValueError):
            continue

        for entry in entries if isinstance(entries, list) else []:
            title = entry.get("dc:title")
            if not title:
                continue
            eid = entry.get("eid")
            evidence.append(
                CandidateEvidence(
                    reviewer_name=candidate.name,
                    affiliation=candidate.affiliation,
                    source="Scopus",
                    paper_title=title,
                    journal_name=entry.get("prism:publicationName"),
                    publication_type="journal article" if entry.get("prism:publicationName") else None,
                    publication_language=_english_language_fallback(title),
                    publication_year=_year(entry.get("prism:coverDate")),
                    doi=_normalize_doi(entry.get("prism:doi")),
                    url=entry.get("prism:url") or _scopus_url(eid),
                    citation_count=_int_or_none(entry.get("citedby-count")),
                    scopus_author_id=candidate.scopus_author_id,
                    scopus_eid=eid,
                    matched_keywords=_matched_terms(title, terms),
                    orcid=candidate.orcid,
                )
            )
    return evidence


def _add_evidence_items(candidate: ReviewerCandidate, evidence_items: list[CandidateEvidence]) -> None:
    for evidence in evidence_items:
        evidence_id = (
            evidence.doi
            or evidence.openalex_work_id
            or evidence.scopus_eid
            or evidence.url
            or evidence.paper_title
        )
        if evidence_id in candidate.publication_ids:
            continue
        candidate.publication_ids.append(evidence_id)
        candidate.matching_papers.append(evidence)
        candidate.source_coverage[evidence.source] = True
        if evidence.orcid:
            candidate.orcid = candidate.orcid or evidence.orcid


def _refresh_candidate_summary(candidate: ReviewerCandidate) -> None:
    count = len(candidate.matching_papers)
    suffix = "paper" if count == 1 else "papers"
    sources = ", ".join(source for source, covered in candidate.source_coverage.items() if covered)
    candidate.evidence_summary = f"{count} matching {suffix} found across: {sources or 'no source coverage'}."


def _matched_terms(text: str, terms: list[str]) -> list[str]:
    matches = []
    for term in terms:
        pattern = r"(?<![A-Za-z0-9])" + re.escape(term.casefold()) + r"(?![A-Za-z0-9])"
        if re.search(pattern, text.casefold()):
            matches.append(term)
    return sorted(set(matches), key=str.casefold)


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        value = value.strip()
        if value and value.casefold() not in {item.casefold() for item in deduped}:
            deduped.append(value)
    return deduped[:24]


def _scopus_query(query: str) -> str:
    return re.sub(r"[(){}\\[\\]\"]", " ", query).strip()


def _openalex_journal_name(work: dict) -> str | None:
    primary_location = work.get("primary_location") or {}
    source = primary_location.get("source") or {}
    if source.get("display_name"):
        return source.get("display_name")
    return None


def _openalex_abstract(work: dict) -> str | None:
    inverted_index = work.get("abstract_inverted_index")
    if not isinstance(inverted_index, dict):
        return None
    positioned_words: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        if not isinstance(positions, list):
            continue
        for position in positions:
            if isinstance(position, int):
                positioned_words.append((position, word))
    if not positioned_words:
        return None
    return " ".join(word for _, word in sorted(positioned_words))[:4000]


def _normalize_publication_type(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.replace("-", " ").replace("_", " ").casefold().strip()
    type_map = {
        "article": "journal article",
        "journal article": "journal article",
        "book chapter": "book chapter",
        "book section": "book chapter",
        "chapter": "book chapter",
        "proceedings article": "conference paper",
    }
    return type_map.get(normalized, normalized)


def _scopus_url(eid: str | None) -> str:
    if not eid:
        return ""
    return f"https://www.scopus.com/record/display.uri?eid={quote(eid, safe='')}"


def _year(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"\b(19|20)\d{2}\b", value)
    return int(match.group(0)) if match else None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    if doi.startswith("https://doi.org/"):
        return doi
    return f"https://doi.org/{quote(doi.removeprefix('doi:'), safe='/:')}"


def _english_language_fallback(title: str) -> str | None:
    ascii_letters = len(re.findall(r"[A-Za-z]", title or ""))
    non_ascii_letters = len(re.findall(r"[^\W\d_]", title or "", flags=re.UNICODE)) - ascii_letters
    if ascii_letters >= 8 and ascii_letters >= non_ascii_letters * 3:
        return "en"
    return None
