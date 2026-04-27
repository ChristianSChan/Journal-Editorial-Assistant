"""Stricter paper-match analysis for reviewer evidence."""

from __future__ import annotations

from pydantic import BaseModel, Field

from services.llm_provider import call_llm_json, llm_enabled
from services.reviewer_retrieval import CandidateEvidence, ReviewerSearchInput

MAX_LLM_PAPERS = 20
MAX_ABSTRACT_CHARS = 1200


class LlmPaperMatch(BaseModel):
    paper_title: str
    topic_match: bool = False
    method_match: bool = False
    matched_terms: list[str] = Field(default_factory=list)
    rationale: str = ""


def analyze_paper_matches_with_llm(
    search_input: ReviewerSearchInput,
    papers: list[CandidateEvidence],
) -> dict[str, LlmPaperMatch]:
    """Return abstract-based topic/method match judgments for papers with abstracts."""
    if not llm_enabled():
        return {}

    abstract_papers = [
        paper
        for paper in papers
        if paper.abstract and paper.paper_title
    ][:MAX_LLM_PAPERS]
    if not abstract_papers:
        return {}

    prompt = (
        "Assess whether retrieved papers match a manuscript for reviewer discovery. "
        "Use only the manuscript title, abstract, keywords, and each paper abstract. "
        "Return JSON only with key paper_matches. Each item must include: "
        "paper_title, topic_match, method_match, matched_terms, rationale. "
        "topic_match should be true only when the paper abstract shares a substantive topic. "
        "method_match should be true only when the paper abstract shares a method, design, or analytic approach. "
        "Do not infer beyond the supplied text."
    )
    payload = {
        "manuscript": {
            "title": search_input.title,
            "abstract": search_input.abstract[:2500],
            "keywords": search_input.keywords,
        },
        "papers": [
            {
                "paper_title": paper.paper_title,
                "paper_abstract": paper.abstract[:MAX_ABSTRACT_CHARS],
                "source": paper.source,
            }
            for paper in abstract_papers
        ],
    }
    data = call_llm_json(prompt, payload, temperature=0)
    if not data:
        return {}

    matches: dict[str, LlmPaperMatch] = {}
    raw_items = data.get("paper_matches", [])
    for item in raw_items if isinstance(raw_items, list) else []:
        try:
            match = LlmPaperMatch(
                paper_title=str(item.get("paper_title", "")),
                topic_match=bool(item.get("topic_match", False)),
                method_match=bool(item.get("method_match", False)),
                matched_terms=[
                    str(term).strip()
                    for term in item.get("matched_terms", [])
                    if str(term).strip()
                ][:8],
                rationale=str(item.get("rationale", "")).strip()[:240],
            )
        except AttributeError:
            continue
        if match.paper_title:
            matches[_paper_key(match.paper_title)] = match
    return matches


def _paper_key(title: str) -> str:
    return " ".join(title.casefold().split())
