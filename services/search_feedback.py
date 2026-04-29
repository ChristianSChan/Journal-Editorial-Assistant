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
FEEDBACK_REASONS = (
    "wrong topic",
    "wrong method",
    "wrong population/context",
    "conflict concern",
    "not recent enough",
    "insufficient evidence",
)
REASON_TERM_KEYS = {
    "wrong topic": "topic_terms",
    "wrong method": "method_terms",
    "wrong population/context": "population_terms",
}


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
    reasons: list[str] | None = None,
    note: str = "",
) -> None:
    if label not in {"useful", "irrelevant"}:
        return

    reasons = [reason for reason in (reasons or []) if reason in FEEDBACK_REASONS]
    search_context = _feedback_context_for_search(search_input)
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
            "search_terms": search_context["all_terms"],
            "search_context": search_context,
            "candidate_terms": _feedback_terms_for_candidate(candidate),
            "candidate_context": _feedback_context_for_candidate(candidate),
            "reasons": reasons,
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
    current_context = _feedback_context_for_search(search_input)
    current_candidate_context = _feedback_context_for_candidate(candidate)
    candidate_terms = set(_feedback_terms_for_candidate(candidate))
    adjustment = 0

    for entry in entries:
        label = entry.get("label")
        exact_person = entry.get("candidate_key") == candidate_key
        previous_terms = set(entry.get("search_terms", [])) | set(entry.get("candidate_terms", []))
        previous_context = entry.get("search_context", {})
        previous_candidate_context = entry.get("candidate_context", {})
        term_overlap = len((current_terms | candidate_terms) & previous_terms)
        context_similarity = _context_similarity(
            current_context,
            current_candidate_context,
            previous_context,
            previous_candidate_context,
        )
        similar_context = term_overlap >= 2 or context_similarity >= 2

        if label == "useful":
            if exact_person:
                adjustment += 4 if similar_context else 1
            elif similar_context:
                adjustment += 1
        elif label == "irrelevant":
            reason_penalty = _reason_specific_penalty(
                entry.get("reasons", []),
                current_context,
                current_candidate_context,
                previous_context,
            )
            if exact_person and similar_context:
                adjustment -= max(2, reason_penalty)
            elif similar_context:
                adjustment -= min(2, reason_penalty)

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


def _feedback_context_for_search(search_input: ReviewerSearchInput) -> dict[str, list[str]]:
    topic_terms = _dedupe_terms([*search_input.keywords, *extract_search_terms(search_input)])
    method_terms = _terms_present(search_input.title + " " + search_input.abstract, _METHOD_TERMS)
    population_terms = _terms_present(search_input.title + " " + search_input.abstract, _POPULATION_TERMS)
    return {
        "topic_terms": topic_terms,
        "method_terms": method_terms,
        "population_terms": population_terms,
        "all_terms": _dedupe_terms([*topic_terms, *method_terms, *population_terms]),
    }


def _feedback_context_for_candidate(candidate: ReviewerCandidate) -> dict[str, list[str]]:
    topic_terms: list[str] = []
    method_terms: list[str] = []
    population_terms: list[str] = []
    for paper in candidate.matching_papers[:8]:
        categories = set(getattr(paper, "match_categories", []) or [])
        terms = [*paper.matched_keywords, *_title_terms(paper.paper_title)]
        if "topic content" in categories or not categories:
            topic_terms.extend(terms)
        if "method" in categories:
            method_terms.extend(terms)
        if "population" in categories:
            population_terms.extend(terms)
        method_terms.extend(_terms_present(paper.paper_title, _METHOD_TERMS))
        population_terms.extend(_terms_present(paper.paper_title, _POPULATION_TERMS))
    return {
        "topic_terms": _dedupe_terms(topic_terms),
        "method_terms": _dedupe_terms(method_terms),
        "population_terms": _dedupe_terms(population_terms),
        "all_terms": _dedupe_terms([*topic_terms, *method_terms, *population_terms]),
    }


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


def _terms_present(text: str, vocabulary: set[str]) -> list[str]:
    normalized = _normalize_term(text)
    found: list[str] = []
    for term in vocabulary:
        pattern = r"\b" + re.escape(term).replace(r"\ ", r"\s+") + r"\b"
        if re.search(pattern, normalized):
            found.append(term)
    return sorted(found)


def _context_similarity(
    current_search_context: dict[str, list[str]],
    current_candidate_context: dict[str, list[str]],
    previous_search_context: dict[str, list[str]],
    previous_candidate_context: dict[str, list[str]],
) -> int:
    score = 0
    for key in ("topic_terms", "method_terms", "population_terms"):
        current_terms = set(current_search_context.get(key, [])) | set(
            current_candidate_context.get(key, [])
        )
        previous_terms = set(previous_search_context.get(key, [])) | set(
            previous_candidate_context.get(key, [])
        )
        if current_terms & previous_terms:
            score += 1
    return score


def _reason_specific_penalty(
    reasons: list[str],
    current_search_context: dict[str, list[str]],
    current_candidate_context: dict[str, list[str]],
    previous_search_context: dict[str, list[str]],
) -> int:
    if not reasons:
        return 2
    penalty = 0
    for reason in reasons:
        key = REASON_TERM_KEYS.get(reason)
        if key:
            current_terms = set(current_search_context.get(key, [])) | set(
                current_candidate_context.get(key, [])
            )
            previous_terms = set(previous_search_context.get(key, []))
            if current_terms & previous_terms:
                penalty += 4
        elif reason == "not recent enough":
            penalty += 1
        elif reason in {"conflict concern", "insufficient evidence"}:
            penalty += 2
    return penalty or 1


def _normalize_term(term: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 -]", " ", term.casefold())).strip()


def _normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


_METHOD_TERMS = {
    "experiment",
    "experimental",
    "longitudinal",
    "meta analysis",
    "meta analytic",
    "survey",
    "interview",
    "qualitative",
    "quantitative",
    "mixed methods",
    "regression",
    "structural equation",
    "multilevel",
    "randomized",
    "trial",
    "scale",
    "measurement",
}


_POPULATION_TERMS = {
    "adolescent",
    "adolescents",
    "adult",
    "adults",
    "children",
    "child",
    "student",
    "students",
    "older adult",
    "older adults",
    "elderly",
    "aging",
    "ageing",
    "chinese",
    "japanese",
    "korean",
    "asian",
    "hong kong",
    "taiwan",
    "community",
    "clinical",
    "patient",
    "patients",
}


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
