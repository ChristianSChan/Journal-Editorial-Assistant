"""Reviewer retrieval service with OpenAlex backbone and multi-source evidence."""

from __future__ import annotations

import re
from collections import Counter
from urllib.parse import quote

from pydantic import BaseModel, Field

MAX_SEARCH_TERMS = 8
STOPWORDS = {
    "about", "after", "among", "analysis", "based", "between", "during",
    "effect", "effects", "from", "into", "journal", "method", "methods",
    "model", "paper", "results", "study", "that", "their", "these", "this",
    "through", "using", "with", "within",
}
SOURCE_NAMES = ("OpenAlex", "Semantic Scholar", "Scopus", "Crossref", "ORCID", "PubMed")


class ReviewerSearchInput(BaseModel):
    title: str = ""
    abstract: str = ""
    keywords: list[str] = Field(default_factory=list)


class CandidateEvidence(BaseModel):
    reviewer_name: str = ""
    affiliation: str | None = None
    source: str
    paper_title: str
    abstract: str | None = None
    journal_name: str | None = None
    publication_type: str | None = None
    publication_language: str | None = None
    publication_year: int | None = None
    doi: str | None = None
    url: str = ""
    openalex_url: str = ""
    citation_count: int | None = None
    matched_keywords: list[str] = Field(default_factory=list)
    match_basis: list[str] = Field(default_factory=list)
    llm_topic_match: bool | None = None
    llm_method_match: bool | None = None
    llm_match_rationale: str = ""
    orcid: str | None = None
    openalex_author_id: str | None = None
    semantic_scholar_author_id: str | None = None
    scopus_author_id: str | None = None
    openalex_work_id: str | None = None
    semantic_scholar_paper_id: str | None = None
    scopus_eid: str | None = None
    pubmed_id: str | None = None


ReviewerEvidence = CandidateEvidence


class ReviewerCandidate(BaseModel):
    name: str
    affiliation: str | None = None
    email: str | None = None
    email_status: str = "Unavailable in retrieved metadata"
    verified_title: str | None = None
    title_status: str = "Not checked"
    position_title: str | None = None
    position_title_status: str = "Not checked"
    identity_verification_url: str | None = None
    official_profile_url: str | None = None
    contact_confidence: str = "low"
    contact_status: str = "Not checked"
    orcid: str | None = None
    source_openalex_author_id: str | None = None
    semantic_scholar_author_id: str | None = None
    scopus_author_id: str | None = None
    source_ids: dict[str, list[str]] = Field(default_factory=dict)
    source_coverage: dict[str, bool] = Field(default_factory=dict)
    conflict_flags: list[str] = Field(default_factory=list)
    evidence_summary: str = ""
    publication_ids: list[str] = Field(default_factory=list)
    matching_papers: list[CandidateEvidence] = Field(default_factory=list)
    keyword_match_last_10_years: bool = False
    matched_recent_keywords: list[str] = Field(default_factory=list)
    recent_keyword_evidence: list[str] = Field(default_factory=list)
    total_citation_count: int | None = None
    total_citation_source: str = "Unavailable"
    h_index: int | None = None
    h_index_source: str = "Unavailable"
    matched_paper_citation_count: int | None = None
    matched_paper_citation_source: str = "Unavailable"
    publication_count: int | None = None
    publication_count_source: str = "Displayed matching publications only"
    recent_activity_year: int | None = None
    citation_metrics_status: str = "Not checked"
    is_editorial_board_member: bool = False
    editorial_board_source: str | None = None
    editorial_board_status: str = "Not checked"


class ReviewerRetrievalError(RuntimeError):
    """Raised when reviewer retrieval cannot complete."""


def extract_search_terms(search_input: ReviewerSearchInput) -> list[str]:
    """Extract a compact set of search terms from title, abstract, and keywords."""
    keyword_terms = [term.strip() for term in search_input.keywords if term.strip()]
    title_terms = _important_terms(search_input.title, limit=6)
    abstract_terms = _important_terms(search_input.abstract, limit=6)

    terms: list[str] = []
    seen: set[str] = set()
    for term in [*keyword_terms, *title_terms, *abstract_terms]:
        normalized = term.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        terms.append(term)
        if len(terms) >= MAX_SEARCH_TERMS:
            break
    return terms


def retrieve_reviewers(search_input: ReviewerSearchInput) -> list[ReviewerCandidate]:
    """Return deduplicated reviewer candidates from OpenAlex and secondary sources."""
    search_terms = extract_search_terms(search_input)
    if not search_terms:
        return []

    from services.source_clients import fetch_all_source_evidence

    evidence = fetch_all_source_evidence(search_input)
    if not evidence:
        raise ReviewerRetrievalError("No source returned reviewer evidence.")
    return _aggregate_evidence(evidence)


def is_verified_candidate(candidate: ReviewerCandidate) -> bool:
    """A displayed candidate must have metadata name and at least one evidence item."""
    if not candidate.name.strip():
        return False
    if not candidate.matching_papers:
        return False
    return all(paper.paper_title.strip() and (paper.url or paper.openalex_url or paper.doi) for paper in candidate.matching_papers)


def _aggregate_evidence(evidence_items: list[CandidateEvidence]) -> list[ReviewerCandidate]:
    candidates: list[ReviewerCandidate] = []
    for evidence in evidence_items:
        if not evidence.reviewer_name.strip() or not evidence.paper_title.strip():
            continue
        evidence.openalex_url = evidence.openalex_url or evidence.url
        candidate = _find_matching_candidate(candidates, evidence)
        if candidate is None:
            candidate = ReviewerCandidate(
                name=evidence.reviewer_name,
                affiliation=evidence.affiliation,
                orcid=evidence.orcid,
                source_openalex_author_id=evidence.openalex_author_id,
                semantic_scholar_author_id=evidence.semantic_scholar_author_id,
                scopus_author_id=evidence.scopus_author_id,
            )
            candidates.append(candidate)
        _merge_evidence(candidate, evidence)

    for candidate in candidates:
        candidate.evidence_summary = _evidence_summary(candidate)

    return [candidate for candidate in candidates if is_verified_candidate(candidate)]


def _find_matching_candidate(candidates: list[ReviewerCandidate], evidence: CandidateEvidence) -> ReviewerCandidate | None:
    for candidate in candidates:
        if evidence.orcid and candidate.orcid and evidence.orcid == candidate.orcid:
            return candidate
        if evidence.semantic_scholar_author_id and candidate.semantic_scholar_author_id == evidence.semantic_scholar_author_id:
            return candidate
        if evidence.scopus_author_id and candidate.scopus_author_id == evidence.scopus_author_id:
            return candidate
        if evidence.openalex_author_id and candidate.source_openalex_author_id == evidence.openalex_author_id:
            return candidate
        if _fallback_same_person(candidate, evidence):
            return candidate
    return None


def _fallback_same_person(candidate: ReviewerCandidate, evidence: CandidateEvidence) -> bool:
    if _normalized_name(candidate.name) != _normalized_name(evidence.reviewer_name):
        return False
    candidate_affiliation = (candidate.affiliation or "").casefold()
    evidence_affiliation = (evidence.affiliation or "").casefold()
    affiliation_overlap = bool(candidate_affiliation and evidence_affiliation and (candidate_affiliation in evidence_affiliation or evidence_affiliation in candidate_affiliation))
    candidate_titles = {_normalized_title(paper.paper_title) for paper in candidate.matching_papers}
    title_overlap = _normalized_title(evidence.paper_title) in candidate_titles
    return affiliation_overlap or title_overlap


def _merge_evidence(candidate: ReviewerCandidate, evidence: CandidateEvidence) -> None:
    if not candidate.affiliation and evidence.affiliation:
        candidate.affiliation = evidence.affiliation
    candidate.orcid = candidate.orcid or evidence.orcid
    candidate.source_openalex_author_id = candidate.source_openalex_author_id or evidence.openalex_author_id
    candidate.semantic_scholar_author_id = candidate.semantic_scholar_author_id or evidence.semantic_scholar_author_id
    candidate.scopus_author_id = candidate.scopus_author_id or evidence.scopus_author_id

    _add_source_id(candidate, "ORCID", evidence.orcid)
    _add_source_id(candidate, "OpenAlex", evidence.openalex_author_id)
    _add_source_id(candidate, "Semantic Scholar", evidence.semantic_scholar_author_id)
    _add_source_id(candidate, "Scopus", evidence.scopus_author_id)
    _add_source_id(candidate, "Scopus", evidence.scopus_eid)
    _add_source_id(candidate, "PubMed", evidence.pubmed_id)
    _add_source_id(candidate, evidence.source, evidence.doi or evidence.url)

    for source in SOURCE_NAMES:
        candidate.source_coverage.setdefault(source, False)
    candidate.source_coverage[evidence.source] = True
    if evidence.orcid:
        candidate.source_coverage["ORCID"] = True

    evidence_id = evidence.doi or evidence.openalex_work_id or evidence.semantic_scholar_paper_id or evidence.scopus_eid or evidence.pubmed_id or evidence.url or evidence.paper_title
    if evidence_id not in candidate.publication_ids:
        candidate.publication_ids.append(evidence_id)
        candidate.matching_papers.append(evidence)


def _add_source_id(candidate: ReviewerCandidate, source: str, value: str | None) -> None:
    if not value:
        return
    ids = candidate.source_ids.setdefault(source, [])
    if value not in ids:
        ids.append(value)


def _important_terms(text: str, limit: int) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z-]{3,}", text.lower())
    counts = Counter(token for token in tokens if token not in STOPWORDS)
    return [term for term, _ in counts.most_common(limit)]


def _evidence_summary(candidate: ReviewerCandidate) -> str:
    count = len(candidate.matching_papers)
    suffix = "paper" if count == 1 else "papers"
    sources = ", ".join(source for source, covered in candidate.source_coverage.items() if covered)
    return f"{count} matching {suffix} found across: {sources or 'no source coverage'}."


def _normalized_name(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.casefold())


def _normalized_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", title.casefold())[:140]


def _normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    doi = doi.strip()
    if doi.startswith("https://doi.org/"):
        return doi
    return f"https://doi.org/{quote(doi.removeprefix('doi:'), safe='/:')}"
