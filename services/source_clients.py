"""Scholarly source clients returning normalized candidate evidence."""

from __future__ import annotations

import os
import re
import time
from urllib.parse import quote

import requests

from services.llm_assist import build_reviewer_search_profile
from services.openalex_config import openalex_headers, openalex_params
from services.reviewer_retrieval import CandidateEvidence, ReviewerSearchInput, extract_search_terms

REQUEST_TIMEOUT_SECONDS = 7
MAX_RESULTS_PER_QUERY = 6
MAX_EVIDENCE_PER_SOURCE = 60

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
SEMANTIC_PAPER_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
CROSSREF_WORKS_URL = "https://api.crossref.org/works"
SCOPUS_SEARCH_URL = "https://api.elsevier.com/content/search/scopus"
ORCID_EXPANDED_SEARCH_URL = "https://pub.orcid.org/v3.0/expanded-search/"
NCBI_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NCBI_ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
CLARIVATE_REVIEWER_LOCATOR_DEFAULT_URL = "https://api.clarivate.com/api/wosrl"
_CLARIVATE_TOKEN_CACHE: dict[str, object] = {"access_token": "", "expires_at": 0.0}


def fetch_all_source_evidence(search_input: ReviewerSearchInput) -> list[CandidateEvidence]:
    """Fetch normalized evidence from all configured scholarly sources."""
    queries = _source_queries(search_input)
    search_terms = extract_search_terms(search_input)
    evidence: list[CandidateEvidence] = []
    for client in (
        fetch_openalex_evidence,
        fetch_semantic_scholar_evidence,
        fetch_scopus_evidence,
        fetch_clarivate_reviewer_locator_evidence,
        fetch_crossref_evidence,
        fetch_pubmed_evidence,
    ):
        try:
            evidence.extend(client(search_input, queries, search_terms))
        except Exception:
            continue

    try:
        enrich_evidence_with_orcid(evidence)
    except Exception:
        pass

    return evidence


def fetch_openalex_evidence(search_input: ReviewerSearchInput, queries: list[str] | None = None, search_terms: list[str] | None = None) -> list[CandidateEvidence]:
    queries = queries or _source_queries(search_input)
    if not queries:
        return []
    evidence: list[CandidateEvidence] = []
    search_terms = search_terms or extract_search_terms(search_input)
    for query in queries:
        response = requests.get(
            OPENALEX_WORKS_URL,
            params=openalex_params({
                "search": query,
                "per-page": MAX_RESULTS_PER_QUERY,
                "sort": "relevance_score:desc",
            }),
            headers=openalex_headers(),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        works = response.json().get("results", [])

        for work in works if isinstance(works, list) else []:
            title = work.get("display_name")
            work_id = work.get("id")
            if not title or not work_id:
                continue
            for authorship in _first_second_last(work.get("authorships", [])):
                author = authorship.get("author") or {}
                name = author.get("display_name")
                author_id = author.get("id")
                if not name or not author_id:
                    continue
                evidence.append(
                    CandidateEvidence(
                        reviewer_name=name,
                        affiliation=_openalex_affiliation(authorship),
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
                        orcid=_normalize_orcid(author.get("orcid")),
                        openalex_author_id=author_id,
                        openalex_work_id=work_id,
                        matched_keywords=_matched_terms(title, search_terms),
                    )
                )
    return _dedupe_evidence(evidence)[:MAX_EVIDENCE_PER_SOURCE]


def fetch_semantic_scholar_evidence(search_input: ReviewerSearchInput, queries: list[str] | None = None, search_terms: list[str] | None = None) -> list[CandidateEvidence]:
    queries = queries or _source_queries(search_input)
    if not queries:
        return []
    fields = "title,abstract,year,url,citationCount,externalIds,authors,venue,journal"
    headers = _semantic_scholar_headers()
    search_terms = search_terms or extract_search_terms(search_input)

    evidence: list[CandidateEvidence] = []
    for query in queries:
        response = requests.get(
            SEMANTIC_PAPER_SEARCH_URL,
            params={"query": query, "limit": MAX_RESULTS_PER_QUERY, "fields": fields},
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        papers = response.json().get("data", [])
        for paper in papers if isinstance(papers, list) else []:
            title = paper.get("title")
            if not title:
                continue
            external_ids = paper.get("externalIds") or {}
            doi = external_ids.get("DOI")
            for author in _first_second_last(paper.get("authors", []) or []):
                name = author.get("name")
                author_id = author.get("authorId")
                if not name:
                    continue
                evidence.append(
                    CandidateEvidence(
                        reviewer_name=name,
                        source="Semantic Scholar",
                        paper_title=title,
                        abstract=paper.get("abstract"),
                        journal_name=_semantic_scholar_journal_name(paper),
                        publication_type="journal article" if _semantic_scholar_journal_name(paper) else None,
                        publication_language="en",
                        publication_year=_int_or_none(paper.get("year")),
                        doi=_normalize_doi(doi),
                        url=paper.get("url") or "",
                        citation_count=_int_or_none(paper.get("citationCount")),
                        semantic_scholar_author_id=str(author_id) if author_id else None,
                        semantic_scholar_paper_id=paper.get("paperId"),
                        matched_keywords=_matched_terms(title, search_terms),
                    )
                )
    return _dedupe_evidence(evidence)[:MAX_EVIDENCE_PER_SOURCE]


def fetch_scopus_evidence(search_input: ReviewerSearchInput, queries: list[str] | None = None, search_terms: list[str] | None = None) -> list[CandidateEvidence]:
    api_key = os.getenv("SCOPUS_API_KEY", "").strip()
    if not api_key:
        return []

    queries = queries or _source_queries(search_input)
    if not queries:
        return []

    evidence: list[CandidateEvidence] = []
    search_terms = search_terms or extract_search_terms(search_input)
    for query in queries:
        response = requests.get(
            SCOPUS_SEARCH_URL,
            params={
                "query": f"TITLE-ABS-KEY({_scopus_query(query)})",
                "count": MAX_RESULTS_PER_QUERY,
                "field": "dc:title,prism:publicationName,prism:coverDate,prism:doi,citedby-count,author,affiliation,eid,prism:url",
            },
            headers={
                "X-ELS-APIKey": api_key,
                "Accept": "application/json",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        entries = response.json().get("search-results", {}).get("entry", [])
        for entry in entries if isinstance(entries, list) else []:
            title = entry.get("dc:title")
            if not title:
                continue
            year = _scopus_year(entry.get("prism:coverDate"))
            doi = _normalize_doi(entry.get("prism:doi"))
            eid = entry.get("eid")
            url = entry.get("prism:url") or _scopus_url(eid)
            affiliations = _scopus_affiliations(entry)
            authors = entry.get("author", []) or []
            for index, author in _first_second_last_with_index(authors):
                name = author.get("authname") or " ".join(
                    part for part in [author.get("given-name"), author.get("surname")] if part
                )
                if not name:
                    continue
                evidence.append(
                    CandidateEvidence(
                        reviewer_name=name,
                        affiliation=affiliations[index] if index < len(affiliations) else _first(affiliations),
                        source="Scopus",
                        paper_title=title,
                        journal_name=entry.get("prism:publicationName"),
                        publication_type="journal article" if entry.get("prism:publicationName") else None,
                        publication_language=_english_language_fallback(title),
                        publication_year=year,
                        doi=doi,
                        url=url,
                        citation_count=_int_or_none(entry.get("citedby-count")),
                        scopus_author_id=author.get("authid"),
                        scopus_eid=eid,
                        matched_keywords=_matched_terms(title, search_terms),
                    )
                )
    return _dedupe_evidence(evidence)[:MAX_EVIDENCE_PER_SOURCE]


def fetch_clarivate_reviewer_locator_evidence(
    search_input: ReviewerSearchInput,
    queries: list[str] | None = None,
    search_terms: list[str] | None = None,
) -> list[CandidateEvidence]:
    headers = _clarivate_headers()
    if not headers:
        return []

    base_url = os.getenv(
        "CLARIVATE_REVIEWER_LOCATOR_BASE_URL",
        CLARIVATE_REVIEWER_LOCATOR_DEFAULT_URL,
    ).strip() or CLARIVATE_REVIEWER_LOCATOR_DEFAULT_URL
    url = base_url.rstrip("/")
    search_terms = search_terms or extract_search_terms(search_input)
    payload = {
        "title": search_input.title,
        "abstract": search_input.abstract,
        "keywords": search_input.keywords,
        "manuscript": {
            "title": search_input.title,
            "abstract": search_input.abstract,
            "keywords": search_input.keywords,
        },
        "limit": MAX_RESULTS_PER_QUERY * 3,
    }
    headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    response = requests.post(
        url,
        json=payload,
        headers=headers,
        timeout=REQUEST_TIMEOUT_SECONDS + 5,
    )
    response.raise_for_status()
    data = response.json()
    reviewers = _clarivate_candidate_items(data)
    evidence: list[CandidateEvidence] = []
    for reviewer in reviewers:
        reviewer_name = _clarivate_person_name(reviewer)
        if not reviewer_name:
            continue
        affiliation = _clarivate_affiliation(reviewer)
        email = _first_string_by_keys(reviewer, ("email", "emailAddress", "primaryEmail", "contactEmail"))
        reviewer_id = _first_string_by_keys(
            reviewer,
            (
                "id",
                "reviewerId",
                "researcherId",
                "wosResearcherId",
                "rid",
                "personId",
                "authorId",
            ),
        )
        publications = _clarivate_publications(reviewer)
        for publication in publications:
            title = _first_string_by_keys(
                publication,
                ("title", "paperTitle", "articleTitle", "publicationTitle", "name"),
            )
            if not title:
                continue
            doi = _normalize_doi(_first_string_by_keys(publication, ("doi", "DOI")))
            url_value = (
                _first_string_by_keys(publication, ("url", "link", "recordUrl", "sourceUrl"))
                or _doi_url(doi)
                or f"{url}#{quote(reviewer_name)}"
            )
            evidence.append(
                CandidateEvidence(
                    reviewer_name=reviewer_name,
                    affiliation=affiliation,
                    email=email,
                    source="Clarivate Reviewer Locator",
                    paper_title=title,
                    abstract=_first_string_by_keys(publication, ("abstract", "summary")),
                    journal_name=_first_string_by_keys(
                        publication,
                        ("journal", "journalName", "sourceTitle", "publicationName", "venue"),
                    ),
                    publication_type=_normalize_publication_type(
                        _first_string_by_keys(publication, ("type", "documentType", "publicationType"))
                    ),
                    publication_language=_first_string_by_keys(publication, ("language", "lang")) or _english_language_fallback(title),
                    publication_year=_clarivate_year(publication),
                    doi=doi,
                    url=url_value,
                    citation_count=_int_or_none(
                        _first_string_by_keys(
                            publication,
                            ("timesCited", "timesCitedCount", "citationCount", "citations"),
                        )
                    ),
                    matched_keywords=_matched_terms(title, search_terms),
                    clarivate_reviewer_id=reviewer_id,
                )
            )
    return _dedupe_evidence(evidence)[:MAX_EVIDENCE_PER_SOURCE]


def _clarivate_headers() -> dict[str, str]:
    api_key = os.getenv("CLARIVATE_REVIEWER_LOCATOR_API_KEY", "").strip()
    if api_key:
        return {"X-ApiKey": api_key, "X-APIKey": api_key}

    access_token = os.getenv("CLARIVATE_REVIEWER_LOCATOR_ACCESS_TOKEN", "").strip()
    if access_token:
        return {"Authorization": f"Bearer {access_token}"}

    token = _clarivate_client_credentials_token()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _clarivate_client_credentials_token() -> str:
    cached_token = str(_CLARIVATE_TOKEN_CACHE.get("access_token") or "")
    expires_at = float(_CLARIVATE_TOKEN_CACHE.get("expires_at") or 0)
    if cached_token and expires_at > time.time() + 60:
        return cached_token

    token_url = os.getenv("CLARIVATE_REVIEWER_LOCATOR_TOKEN_URL", "").strip()
    client_id = os.getenv("CLARIVATE_REVIEWER_LOCATOR_CLIENT_ID", "").strip()
    client_secret = os.getenv("CLARIVATE_REVIEWER_LOCATOR_CLIENT_SECRET", "").strip()
    scope = os.getenv("CLARIVATE_REVIEWER_LOCATOR_SCOPE", "").strip()
    if not (token_url and client_id and client_secret):
        return ""

    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if scope:
        data["scope"] = scope
    response = requests.post(
        token_url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    token_payload = response.json()
    access_token = str(token_payload.get("access_token") or "").strip()
    if not access_token:
        return ""
    expires_in = _int_or_none(token_payload.get("expires_in")) or 3600
    _CLARIVATE_TOKEN_CACHE["access_token"] = access_token
    _CLARIVATE_TOKEN_CACHE["expires_at"] = time.time() + expires_in
    return access_token


def fetch_crossref_evidence(search_input: ReviewerSearchInput, queries: list[str] | None = None, search_terms: list[str] | None = None) -> list[CandidateEvidence]:
    queries = queries or _source_queries(search_input)
    if not queries:
        return []
    search_terms = search_terms or extract_search_terms(search_input)

    evidence: list[CandidateEvidence] = []
    for query in queries:
        response = requests.get(
            CROSSREF_WORKS_URL,
            params={"query.bibliographic": query, "rows": MAX_RESULTS_PER_QUERY},
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": "journal-review-tool/0.1"},
        )
        response.raise_for_status()
        items = response.json().get("message", {}).get("items", [])
        for item in items if isinstance(items, list) else []:
            title = _first(item.get("title"))
            doi = item.get("DOI")
            if not title:
                continue
            for author in _first_second_last(item.get("author", []) or []):
                name = _crossref_author_name(author)
                if not name:
                    continue
                evidence.append(
                    CandidateEvidence(
                        reviewer_name=name,
                        affiliation=_crossref_affiliation(author),
                        source="Crossref",
                        paper_title=title,
                        abstract=_clean_crossref_abstract(item.get("abstract")),
                        journal_name=_first(item.get("container-title")),
                        publication_type=_normalize_publication_type(item.get("type")),
                        publication_language=item.get("language") or _english_language_fallback(title),
                        publication_year=_crossref_year(item),
                        doi=_normalize_doi(doi),
                        url=_doi_url(doi),
                        orcid=_normalize_orcid(author.get("ORCID")),
                        matched_keywords=_matched_terms(title, search_terms),
                    )
                )
    return _dedupe_evidence(evidence)[:MAX_EVIDENCE_PER_SOURCE]


def fetch_pubmed_evidence(search_input: ReviewerSearchInput, queries: list[str] | None = None, search_terms: list[str] | None = None) -> list[CandidateEvidence]:
    queries = queries or _source_queries(search_input)
    if not queries:
        return []
    ids: list[str] = []
    for query in queries:
        search_response = requests.get(
            NCBI_ESEARCH_URL,
            params={"db": "pubmed", "term": query, "retmode": "json", "retmax": MAX_RESULTS_PER_QUERY},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        search_response.raise_for_status()
        for pmid in search_response.json().get("esearchresult", {}).get("idlist", []):
            if pmid not in ids:
                ids.append(pmid)
    if not ids:
        return []

    summary_response = requests.get(
        NCBI_ESUMMARY_URL,
        params={"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    summary_response.raise_for_status()
    result = summary_response.json().get("result", {})
    search_terms = search_terms or extract_search_terms(search_input)

    evidence: list[CandidateEvidence] = []
    for pmid in ids:
        item = result.get(str(pmid), {})
        title = item.get("title")
        if not title:
            continue
        doi = _pubmed_doi(item)
        for author in _first_second_last(item.get("authors", []) or []):
            name = author.get("name")
            if not name:
                continue
            evidence.append(
                CandidateEvidence(
                    reviewer_name=name,
                    source="PubMed",
                    paper_title=title,
                    journal_name=item.get("fulljournalname") or item.get("source"),
                    publication_type="journal article",
                    publication_language=_pubmed_language(item) or _english_language_fallback(title),
                    publication_year=_pubmed_year(item.get("pubdate")),
                    doi=_normalize_doi(doi),
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    pubmed_id=str(pmid),
                    matched_keywords=_matched_terms(title, search_terms),
                )
            )
    return _dedupe_evidence(evidence)[:MAX_EVIDENCE_PER_SOURCE]


def enrich_evidence_with_orcid(evidence: list[CandidateEvidence]) -> None:
    seen_names: set[str] = set()
    for item in evidence:
        if item.orcid or item.reviewer_name in seen_names:
            continue
        seen_names.add(item.reviewer_name)
        orcid = _find_orcid(item.reviewer_name, item.affiliation)
        if not orcid:
            continue
        for related in evidence:
            if _normalized_name(related.reviewer_name) == _normalized_name(item.reviewer_name):
                related.orcid = orcid


def _find_orcid(name: str, affiliation: str | None) -> str | None:
    parts = name.split()
    if len(parts) < 2:
        return None
    query = f'given-names:"{parts[0]}" AND family-name:"{parts[-1]}"'
    if affiliation:
        query += f' AND affiliation-org-name:"{affiliation.split(",")[0]}"'
    response = requests.get(
        ORCID_EXPANDED_SEARCH_URL,
        params={"q": query, "rows": 1},
        headers={"Accept": "application/json"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    results = response.json().get("expanded-result", [])
    if not results:
        return None
    return _normalize_orcid(results[0].get("orcid-id"))


def _source_queries(search_input: ReviewerSearchInput) -> list[str]:
    return build_reviewer_search_profile(search_input).queries


def _scopus_query(query: str) -> str:
    return re.sub(r"[(){}\\[\\]\"]", " ", query).strip()


def _scopus_affiliations(entry: dict) -> list[str]:
    affiliations = entry.get("affiliation") or []
    values: list[str] = []
    for affiliation in affiliations if isinstance(affiliations, list) else []:
        name = affiliation.get("affilname") or affiliation.get("affiliation-name")
        city = affiliation.get("affiliation-city")
        country = affiliation.get("affiliation-country")
        parts = [part for part in [name, city, country] if part]
        if parts:
            values.append(", ".join(parts))
    return values


def _scopus_year(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"\b(19|20)\d{2}\b", value)
    return int(match.group(0)) if match else None


def _scopus_url(eid: str | None) -> str:
    if not eid:
        return ""
    return f"https://www.scopus.com/record/display.uri?eid={quote(eid, safe='')}"


def _clarivate_candidate_items(data: object) -> list[dict]:
    containers = _candidate_containers(data)
    reviewers: list[dict] = []
    for container in containers:
        if isinstance(container, list):
            reviewers.extend(item for item in container if isinstance(item, dict))
    if isinstance(data, list):
        reviewers.extend(item for item in data if isinstance(item, dict))
    if isinstance(data, dict) and not reviewers:
        for value in data.values():
            if isinstance(value, list) and any(isinstance(item, dict) for item in value):
                reviewers.extend(item for item in value if isinstance(item, dict))
    return reviewers


def _candidate_containers(data: object) -> list[object]:
    if not isinstance(data, dict):
        return []
    keys = (
        "reviewers",
        "candidates",
        "recommendations",
        "suggestions",
        "items",
        "results",
        "data",
        "authors",
    )
    containers = [data.get(key) for key in keys if key in data]
    nested = data.get("response") or data.get("result") or data.get("payload")
    if isinstance(nested, dict):
        containers.extend(_candidate_containers(nested))
    return containers


def _clarivate_person_name(reviewer: dict) -> str:
    direct = _first_string_by_keys(
        reviewer,
        ("name", "fullName", "displayName", "preferredName", "reviewerName", "researcherName"),
    )
    if direct:
        return direct
    first = _first_string_by_keys(reviewer, ("firstName", "givenName", "givenNames"))
    last = _first_string_by_keys(reviewer, ("lastName", "familyName", "surname"))
    return " ".join(part for part in (first, last) if part).strip()


def _clarivate_affiliation(reviewer: dict) -> str | None:
    direct = _first_string_by_keys(
        reviewer,
        ("affiliation", "currentAffiliation", "institution", "organization", "organisation"),
    )
    if direct:
        return direct
    affiliations = reviewer.get("affiliations") or reviewer.get("organizations")
    if isinstance(affiliations, list):
        values = []
        for affiliation in affiliations:
            if isinstance(affiliation, str):
                values.append(affiliation)
            elif isinstance(affiliation, dict):
                value = _first_string_by_keys(affiliation, ("name", "organization", "institution", "displayName"))
                if value:
                    values.append(value)
        return ", ".join(values[:2]) or None
    return None


def _clarivate_publications(reviewer: dict) -> list[dict]:
    keys = (
        "publications",
        "matchedPublications",
        "matchingPublications",
        "matchedPapers",
        "papers",
        "works",
        "articles",
        "relevantPublications",
        "recentPublications",
    )
    publications: list[dict] = []
    for key in keys:
        value = reviewer.get(key)
        if isinstance(value, list):
            publications.extend(item for item in value if isinstance(item, dict))
    return publications


def _clarivate_year(publication: dict) -> int | None:
    for key in ("year", "publicationYear", "publishedYear", "pubYear"):
        value = publication.get(key)
        year = _int_or_none(value)
        if year:
            return year
    for key in ("publicationDate", "publishedDate", "date", "coverDate"):
        value = publication.get(key)
        if isinstance(value, str):
            match = re.search(r"\b(19|20)\d{2}\b", value)
            if match:
                return int(match.group(0))
    return None


def _first_string_by_keys(item: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, dict):
            nested = _first_string_by_keys(value, ("value", "name", "displayName", "url"))
            if nested:
                return nested
    return None


def _dedupe_evidence(evidence: list[CandidateEvidence]) -> list[CandidateEvidence]:
    deduped: list[CandidateEvidence] = []
    seen: set[tuple[str, str, str]] = set()
    for item in evidence:
        key = (_normalized_name(item.reviewer_name), item.source, _normalized_title(item.paper_title))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _matched_terms(text: str, terms: list[str]) -> list[str]:
    matches: list[str] = []
    for term in terms:
        if not term.strip():
            continue
        pattern = r"(?<![A-Za-z0-9])" + re.escape(term.casefold()) + r"(?![A-Za-z0-9])"
        if re.search(pattern, text.casefold()):
            matches.append(term)
    return matches


def _first_second_last(items: list[dict]) -> list[dict]:
    return [item for _, item in _first_second_last_with_index(items)]


def _first_second_last_with_index(items: list[dict]) -> list[tuple[int, dict]]:
    if not isinstance(items, list):
        return []
    selected_indices = [index for index in (0, 1, len(items) - 1) if 0 <= index < len(items)]
    deduped_indices = sorted(set(selected_indices))
    return [
        (index, items[index])
        for index in deduped_indices
        if isinstance(items[index], dict)
    ]


def _semantic_scholar_headers() -> dict[str, str]:
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    return {"x-api-key": api_key} if api_key else {}


def _openalex_affiliation(authorship: dict) -> str | None:
    institutions = authorship.get("institutions") or []
    names = [institution.get("display_name") for institution in institutions if institution.get("display_name")]
    if names:
        return ", ".join(names)
    affiliations = authorship.get("affiliations") or []
    raw = [affiliation.get("raw_affiliation_string") for affiliation in affiliations if affiliation.get("raw_affiliation_string")]
    return raw[0] if raw else None


def _openalex_journal_name(work: dict) -> str | None:
    primary_location = work.get("primary_location") or {}
    source = primary_location.get("source") or {}
    if source.get("display_name"):
        return source.get("display_name")
    locations = work.get("locations") or []
    for location in locations if isinstance(locations, list) else []:
        source = location.get("source") or {}
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


def _clean_crossref_abstract(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", text).strip()[:4000] or None


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


def _semantic_scholar_journal_name(paper: dict) -> str | None:
    journal = paper.get("journal") or {}
    if isinstance(journal, dict) and journal.get("name"):
        return journal.get("name")
    return paper.get("venue")


def _crossref_author_name(author: dict) -> str:
    name = author.get("name")
    if name:
        return str(name)
    return " ".join(part for part in [author.get("given"), author.get("family")] if part)


def _crossref_affiliation(author: dict) -> str | None:
    affiliations = author.get("affiliation") or []
    names = [item.get("name") for item in affiliations if item.get("name")]
    return ", ".join(names) if names else None


def _crossref_year(item: dict) -> int | None:
    for key in ("published-print", "published-online", "published", "created"):
        parts = item.get(key, {}).get("date-parts")
        if parts and parts[0]:
            return _int_or_none(parts[0][0])
    return None


def _pubmed_doi(item: dict) -> str | None:
    for article_id in item.get("articleids", []) or []:
        if article_id.get("idtype") == "doi":
            return article_id.get("value")
    return None


def _pubmed_language(item: dict) -> str | None:
    lang = item.get("lang") or item.get("language")
    if isinstance(lang, list) and lang:
        return str(lang[0])
    if isinstance(lang, str):
        return lang
    return None


def _pubmed_year(pubdate: str | None) -> int | None:
    if not pubdate:
        return None
    match = re.search(r"\b(19|20)\d{2}\b", pubdate)
    return int(match.group(0)) if match else None


def _first(value: object) -> str | None:
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str):
        return value
    return None


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    doi = doi.strip()
    if doi.startswith("https://doi.org/"):
        return doi
    return _doi_url(doi.removeprefix("doi:"))


def _doi_url(doi: str | None) -> str | None:
    if not doi:
        return None
    return f"https://doi.org/{quote(doi, safe='/:')}"


def _normalize_orcid(orcid: str | None) -> str | None:
    if not orcid:
        return None
    return orcid.replace("https://orcid.org/", "").strip()


def _normalized_name(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.casefold())


def _normalized_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", title.casefold())[:140]


def _english_language_fallback(title: str) -> str | None:
    if not title:
        return None
    ascii_letters = len(re.findall(r"[A-Za-z]", title))
    non_ascii_letters = len(re.findall(r"[^\W\d_]", title, flags=re.UNICODE)) - ascii_letters
    if ascii_letters >= 8 and ascii_letters >= non_ascii_letters * 3:
        return "en"
    return None
