"""Invitation opener drafting service."""

from __future__ import annotations

import re
from datetime import date

from services.identity_verification import salutation
from services.llm_provider import call_llm_text, last_llm_error, llm_enabled
from services.reviewer_retrieval import ReviewerCandidate, ReviewerSearchInput

MAX_EVIDENCE_ITEMS = 2
_LAST_INVITATION_LLM_ERROR = ""


def draft_invitation_opener(
    candidate: ReviewerCandidate,
    search_input: ReviewerSearchInput,
    journal_name: str = "",
) -> str:
    """Draft a concise opener using only displayed publication evidence."""
    manuscript_title = search_input.title.strip() or "this manuscript"
    greeting = salutation(candidate)
    journal_phrase = f" for {journal_name.strip()}" if journal_name.strip() else ""
    paper_titles = _recent_evidence_titles(candidate)
    topics = _evidence_items(candidate)

    if paper_titles:
        evidence_sentence = (
            f"I came across your recent work, including {_join_evidence(paper_titles)}, "
            "while looking for reviewers with relevant publication evidence"
        )
        if topics:
            evidence_sentence += f" on {_join_evidence(topics)}"
        evidence_sentence += "."
    elif topics:
        evidence_sentence = (
            f"I came across your publication record on {_join_evidence(topics)} "
            "while looking for reviewers with relevant publication evidence."
        )
    else:
        return (
            f"{greeting}, I am writing to ask whether you might be willing to review "
            f'"{manuscript_title}"{journal_phrase}. Your name was identified from '
            "retrieved publication metadata; I would of course understand if the topic "
            "or timing is not a good fit."
        )

    return (
        f"{greeting}, I am writing to ask whether you might be willing to review "
        f'"{manuscript_title}"{journal_phrase}. {evidence_sentence} '
        "The manuscript seems close enough to that area that your perspective could be "
        "helpful, while still leaving room for an independent assessment. I would be "
        "grateful to know whether this is something you could consider."
    )


def draft_invitation_opener_with_llm(
    candidate: ReviewerCandidate,
    search_input: ReviewerSearchInput,
    journal_name: str = "",
) -> str | None:
    """Use an LLM to improve specificity while using only verified evidence."""
    _set_last_invitation_llm_error("")
    if not llm_enabled():
        _set_last_invitation_llm_error("LLM provider is not enabled.")
        return None

    evidence = _llm_evidence(candidate)
    if not evidence:
        _set_last_invitation_llm_error("No recent verified paper evidence was available for this reviewer.")
        return None

    prompt = (
        "Draft one short opening paragraph for a journal peer-review invitation. "
        "Use only the supplied verified reviewer evidence. Do not invent titles, "
        "email addresses, affiliations, papers, expertise, or relationships. "
        "Mention the manuscript title. Mention 1-2 verified paper titles or topics "
        "from the evidence. Avoid exaggerated praise. Do not say 'uniquely qualified'. "
        "Keep the tone collegial, concise, and editorial. Return only the paragraph."
    )
    user_payload = {
        "greeting": salutation(candidate),
        "journal_name": journal_name,
        "manuscript_title": search_input.title.strip() or "this manuscript",
        "manuscript_abstract": search_input.abstract[:1800],
        "manuscript_keywords": search_input.keywords,
        "reviewer_name": candidate.name,
        "reviewer_affiliation": candidate.affiliation,
        "verified_evidence": evidence,
    }
    content = call_llm_text(prompt, user_payload, temperature=0.2)
    if not content:
        _set_last_invitation_llm_error(last_llm_error() or "LLM returned no content.")
        return None

    opener = _clean_llm_opener(content)
    if not _mentions_allowed_evidence(opener, evidence):
        _set_last_invitation_llm_error(
            "LLM response was rejected because it did not clearly mention supplied evidence."
        )
        return None
    return opener


def last_invitation_llm_error() -> str:
    return _LAST_INVITATION_LLM_ERROR


def _evidence_items(candidate: ReviewerCandidate) -> list[str]:
    items: list[str] = []

    for paper in candidate.matching_papers:
        if paper.matched_keywords:
            for keyword in paper.matched_keywords:
                if keyword not in items:
                    items.append(keyword)
                if len(items) >= MAX_EVIDENCE_ITEMS:
                    return items

    for paper in candidate.matching_papers:
        title = paper.paper_title.strip()
        if title and title not in items:
            items.append(f'"{title}"')
        if len(items) >= MAX_EVIDENCE_ITEMS:
            break

    return items


def _join_evidence(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return f"{items[0]} and {items[1]}"


def _recent_evidence_titles(candidate: ReviewerCandidate) -> list[str]:
    current_year = date.today().year
    papers = sorted(
        candidate.matching_papers,
        key=lambda paper: paper.publication_year or 0,
        reverse=True,
    )
    titles: list[str] = []
    for paper in papers:
        if paper.publication_year is not None and paper.publication_year < current_year - 10:
            continue
        title = paper.paper_title.strip()
        if title and title not in titles:
            titles.append(f'"{title}"')
        if len(titles) >= MAX_EVIDENCE_ITEMS:
            break
    return titles


def _llm_evidence(candidate: ReviewerCandidate) -> list[dict[str, object]]:
    current_year = date.today().year
    papers = sorted(
        candidate.matching_papers,
        key=lambda paper: (
            bool(paper.matched_keywords),
            paper.publication_year or 0,
            paper.citation_count or 0,
        ),
        reverse=True,
    )
    evidence: list[dict[str, object]] = []
    for paper in papers:
        if paper.publication_year is not None and paper.publication_year < current_year - 10:
            continue
        if not paper.paper_title.strip():
            continue
        evidence.append(
            {
                "paper_title": paper.paper_title,
                "year": paper.publication_year,
                "journal": paper.journal_name,
                "publication_type": paper.publication_type,
                "matched_terms": paper.matched_keywords,
                "source": paper.source,
            }
        )
        if len(evidence) >= 4:
            break
    return evidence


def _clean_llm_opener(value: str) -> str:
    value = re.sub(r"^```(?:text)?\s*", "", value.strip())
    value = re.sub(r"\s*```$", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:1200]


def _mentions_allowed_evidence(opener: str, evidence: list[dict[str, object]]) -> bool:
    opener_casefold = opener.casefold()
    if "uniquely qualified" in opener_casefold:
        return False
    evidence_terms: list[str] = []
    for item in evidence:
        title = str(item.get("paper_title") or "")
        evidence_terms.extend(_title_chunks(title))
        evidence_terms.extend(str(term) for term in item.get("matched_terms") or [])
    return any(term.casefold() in opener_casefold for term in evidence_terms if len(term) >= 5)


def _title_chunks(title: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z-]{3,}", title)
    chunks: list[str] = []
    for index in range(0, max(len(words) - 1, 0)):
        chunks.append(" ".join(words[index:index + 2]))
    return chunks or words


def _set_last_invitation_llm_error(message: str) -> None:
    global _LAST_INVITATION_LLM_ERROR
    _LAST_INVITATION_LLM_ERROR = message
