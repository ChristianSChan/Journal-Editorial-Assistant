"""Local feedback learning for reviewer search results."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from services.reviewer_retrieval import ReviewerCandidate, ReviewerSearchInput, extract_search_terms

FEEDBACK_PATH = Path(__file__).resolve().parents[1] / ".journal_review_feedback.json"
MAX_FEEDBACK_ENTRIES = 1_000


def feedback_summary() -> dict[str, int]:
    entries = _load_entries()
    return {
        "total": len(entries),
        "useful": sum(1 for entry in entries if entry.get("label") == "useful"),
        "irrelevant": sum(1 for entry in entries if entry.get("label") == "irrelevant"),
    }


def record_candidate_feedback(
    candidate: ReviewerCandidate,
    search_input: ReviewerSearchInput,
    label: str,
    note: str = "",
) -> None:
    if label not in {"useful", "irrelevant"}:
        return

    entries = _load_entries()
    entries.append(
        {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "label": label,
            "note": note.strip(),
            "candidate_key": candidate_learning_key(candidate),
            "name": candidate.name,
            "affiliation": candidate.affiliation,
            "orcid": candidate.orcid,
            "source_ids": candidate.source_ids,
            "search_terms": _feedback_terms_for_search(search_input),
            "candidate_terms": _feedback_terms_for_candidate(candidate),
            "matched_papers": [
                paper.paper_title for paper in candidate.matching_papers[:5] if paper.paper_title
            ],
        }
    )
    _save_entries(entries[-MAX_FEEDBACK_ENTRIES:])


def feedback_adjustment(
    candidate: ReviewerCandidate,
    search_input: ReviewerSearchInput,
) -> int:
    """Return a small ranking boost or penalty from previous user feedback."""
    entries = _load_entries()
    if not entries:
        return 0

    candidate_key = candidate_learning_key(candidate)
    current_terms = set(_feedback_terms_for_search(search_input))
    candidate_terms = set(_feedback_terms_for_candidate(candidate))
    adjustment = 0

    for entry in entries:
        label = entry.get("label")
        exact_person = entry.get("candidate_key") == candidate_key
        previous_terms = set(entry.get("search_terms", [])) | set(entry.get("candidate_terms", []))
        term_overlap = len((current_terms | candidate_terms) & previous_terms)
        similar_context = term_overlap >= 2

        if label == "useful":
            if exact_person:
                adjustment += 4
            elif similar_context:
                adjustment += 1
        elif label == "irrelevant":
            if exact_person:
                adjustment -= 8
            elif similar_context:
                adjustment -= 2

    return adjustment


def candidate_learning_key(candidate: ReviewerCandidate) -> str:
    stable_id = (
        candidate.orcid
        or candidate.scopus_author_id
        or candidate.semantic_scholar_author_id
        or candidate.source_openalex_author_id
    )
    if stable_id:
        return _normalize_token(stable_id)
    return _normalize_token(f"{candidate.name} {candidate.affiliation or ''}")


def _feedback_terms_for_search(search_input: ReviewerSearchInput) -> list[str]:
    terms = [*search_input.keywords, *extract_search_terms(search_input)]
    return _dedupe_terms(terms)


def _feedback_terms_for_candidate(candidate: ReviewerCandidate) -> list[str]:
    terms: list[str] = [*candidate.matched_recent_keywords]
    for paper in candidate.matching_papers[:8]:
        terms.extend(paper.matched_keywords)
        terms.extend(_title_terms(paper.paper_title))
    return _dedupe_terms(terms)


def _title_terms(title: str) -> list[str]:
    return [
        term
        for term in re.findall(r"[A-Za-z][A-Za-z-]{3,}", title.casefold())
        if term not in {"with", "from", "into", "that", "this", "study", "using"}
    ][:8]


def _dedupe_terms(terms: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        normalized = _normalize_term(term)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped[:30]


def _normalize_term(term: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 -]", " ", term.casefold())).strip()


def _normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _load_entries() -> list[dict[str, Any]]:
    if not FEEDBACK_PATH.exists():
        return []
    try:
        data = json.loads(FEEDBACK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [entry for entry in data if isinstance(entry, dict)]


def _save_entries(entries: list[dict[str, Any]]) -> None:
    FEEDBACK_PATH.write_text(
        json.dumps(entries, indent=2, sort_keys=True),
        encoding="utf-8",
    )
