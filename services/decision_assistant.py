"""LLM-assisted editorial decision analysis and paragraph drafting."""

from __future__ import annotations

from pydantic import BaseModel, Field

from services.llm_provider import call_llm_json, call_llm_text, last_llm_error, llm_enabled
from services.statistical_checking import (
    checks_to_dicts,
    run_statistical_checks,
    summarize_statistical_checks,
)

DECISION_OPTIONS = ["Accept", "Minor revision", "Major revision", "Reject"]


class ReviewerDecisionInput(BaseModel):
    reviewer_label: str
    recommendation: str
    comments: str


class DecisionAnalysis(BaseModel):
    manuscript_summary: str = ""
    recommended_decision: str = "Major revision"
    recommendation_rationale: str = ""
    reviewer_consensus_summary: str = ""
    reviewer_convergence: list[str] = Field(default_factory=list)
    reviewer_divergence: list[str] = Field(default_factory=list)
    main_concerns: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    author_response_assessment: str = ""
    has_author_response: bool = False
    statistics_flags: list[str] = Field(default_factory=list)
    statistical_checks: list[dict[str, object]] = Field(default_factory=list)
    open_science_flags: list[str] = Field(default_factory=list)
    editor_attention_points: list[str] = Field(default_factory=list)
    previous_round_assessment: list[str] = Field(default_factory=list)
    uncertainty: str = ""
    used_llm: bool = False
    error: str = ""


def analyze_decision_materials(
    journal_name: str,
    manuscript_title: str,
    abstract: str,
    manuscript_text: str,
    author_response_text: str,
    reviewer_inputs: list[ReviewerDecisionInput],
    previous_decision_record_text: str = "",
) -> DecisionAnalysis:
    """Analyze reviewer comments and materials for an editorial recommendation."""
    statistical_checks = run_statistical_checks(manuscript_text)
    statistical_flags = summarize_statistical_checks(statistical_checks)
    statistical_check_details = checks_to_dicts(statistical_checks)

    if not llm_enabled():
        return _heuristic_analysis(
            reviewer_inputs,
            has_author_response=bool(author_response_text.strip()),
            statistics_flags=statistical_flags,
            statistical_checks=statistical_check_details,
            error="LLM provider is not enabled.",
        )

    prompt = (
        "You are helping a journal editor make an editorial decision. "
        "Use the manuscript information, reviewer recommendations/comments, and author response if provided. "
        "Return JSON only with keys: manuscript_summary, recommended_decision, recommendation_rationale, "
        "reviewer_consensus_summary, reviewer_convergence, reviewer_divergence, main_concerns, strengths, "
        "author_response_assessment, statistics_flags, open_science_flags, editor_attention_points, "
        "previous_round_assessment, uncertainty. "
        "recommended_decision must be one of: Accept, Minor revision, Major revision, Reject. "
        "The manuscript_summary must be one paragraph and identify topic, methods/design, number of studies, "
        "sample size(s), and primary evidence if these are available; clearly mark unavailable items. "
        "The reviewer_consensus_summary should be detailed enough for a busy editor who will not read each comment. "
        "For main_concerns, indicate which reviewer(s) raised each concern, e.g. 'Reviewers 1 and 3: ...'. "
        "For reviewer_convergence, identify concerns or praises shared by multiple reviewers. "
        "For reviewer_divergence, identify points where reviewers disagree or emphasize different issues. "
        "If no author response is supplied, treat this as a first-round decision and leave author_response_assessment empty. "
        "If prior_round_record is supplied, compare the current manuscript and author response against each prior concern, "
        "especially prior reviewer comments and selected editor highlights. In previous_round_assessment, list which prior "
        "concerns appear addressed, partially addressed, not addressed, or unclear, and identify the prior reviewer/comment "
        "where possible. "
        "Flag potential statistics inconsistencies or errors in statistics_flags, including suspicious sample sizes, "
        "inconsistent p-values/effect sizes/dfs, unclear models, multiple-testing issues, or underpowered analyses. "
        "Use the supplied deterministic_statistical_check as concrete evidence; preserve those findings and add "
        "your own caveats or broader concerns where warranted. "
        "Flag open science practices in open_science_flags, including whether preregistration, data sharing, code sharing, "
        "materials sharing, repository links, or availability statements are present, absent, or unclear. "
        "Separate reviewer concerns from your independent editorial assessment. "
        "Do not overstate certainty. Do not invent facts not present in the supplied material."
    )
    has_author_response = bool(author_response_text.strip())
    payload = {
        "journal_name": journal_name,
        "manuscript_title": manuscript_title,
        "abstract": abstract,
        "manuscript_text_excerpt": manuscript_text[:9000],
        "author_response_text_excerpt": author_response_text[:7000],
        "has_author_response": has_author_response,
        "prior_round_record": previous_decision_record_text[:9000],
        "deterministic_statistical_check": {
            "summary_flags": statistical_flags,
            "checked_items": statistical_check_details[:30],
        },
        "reviewers": [reviewer.model_dump() for reviewer in reviewer_inputs],
    }
    data = call_llm_json(prompt, payload, temperature=0)
    if not data:
        return _heuristic_analysis(
            reviewer_inputs,
            has_author_response=has_author_response,
            statistics_flags=statistical_flags,
            statistical_checks=statistical_check_details,
            error=last_llm_error() or "LLM analysis failed or returned invalid JSON.",
        )

    llm_statistics_flags = _clean_list(data.get("statistics_flags", []))
    return DecisionAnalysis(
        manuscript_summary=str(data.get("manuscript_summary", "")).strip(),
        recommended_decision=_clean_decision(data.get("recommended_decision")),
        recommendation_rationale=str(data.get("recommendation_rationale", "")).strip(),
        reviewer_consensus_summary=str(data.get("reviewer_consensus_summary", "")).strip(),
        reviewer_convergence=_clean_list(data.get("reviewer_convergence", [])),
        reviewer_divergence=_clean_list(data.get("reviewer_divergence", [])),
        main_concerns=_clean_list(data.get("main_concerns", [])),
        strengths=_clean_list(data.get("strengths", [])),
        author_response_assessment=str(data.get("author_response_assessment", "")).strip() if has_author_response else "",
        has_author_response=has_author_response,
        statistics_flags=_merge_flags(statistical_flags, llm_statistics_flags),
        statistical_checks=statistical_check_details,
        open_science_flags=_clean_list(data.get("open_science_flags", [])),
        editor_attention_points=_clean_list(data.get("editor_attention_points", [])),
        previous_round_assessment=_clean_list(data.get("previous_round_assessment", [])),
        uncertainty=str(data.get("uncertainty", "")).strip(),
        used_llm=True,
    )


def draft_decision_paragraphs(
    journal_name: str,
    manuscript_title: str,
    selected_decision: str,
    editor_points: str,
    analysis: DecisionAnalysis,
    reviewer_inputs: list[ReviewerDecisionInput],
) -> str:
    """Draft decision-justification paragraphs, not a full letter."""
    selected_decision = _clean_decision(selected_decision)
    if not llm_enabled():
        return _heuristic_paragraphs(selected_decision, editor_points, analysis)

    prompt = (
        "Draft concise editorial decision-justification paragraphs, not a complete letter. "
        "The editor has already chosen the decision. Use that decision, summarize reviewer concerns, "
        "and integrate the editor's own points. Do not repeat reviewer comments verbatim. "
        "Mention convergence/divergence between reviewers when it helps justify the decision. "
        "If there is no author response, do not discuss author-response adequacy. "
        "If statistics or open-science flags are relevant to the decision, integrate them succinctly. "
        "Do not include salutation, manuscript metadata block, reviewer-by-reviewer lists, closing, or signature. "
        "Tone should be professional, clear, and suitable for a journal decision letter."
    )
    payload = {
        "journal_name": journal_name,
        "manuscript_title": manuscript_title,
        "selected_decision": selected_decision,
        "editor_points": editor_points,
        "analysis": analysis.model_dump(),
        "reviewers": [reviewer.model_dump() for reviewer in reviewer_inputs],
    }
    content = call_llm_text(prompt, payload, temperature=0.2)
    if not content:
        return _heuristic_paragraphs(selected_decision, editor_points, analysis)
    return content.strip()


def _heuristic_analysis(
    reviewer_inputs: list[ReviewerDecisionInput],
    has_author_response: bool,
    statistics_flags: list[str] | None = None,
    statistical_checks: list[dict[str, object]] | None = None,
    error: str = "",
) -> DecisionAnalysis:
    recommendations = [reviewer.recommendation for reviewer in reviewer_inputs if reviewer.comments.strip()]
    recommended = _majority_decision(recommendations)
    concerns = []
    for reviewer in reviewer_inputs:
        first_sentence = reviewer.comments.strip().split(".")[0].strip()
        if first_sentence:
            concerns.append(f"{reviewer.reviewer_label}: {first_sentence}")
    return DecisionAnalysis(
        manuscript_summary="Manuscript summary unavailable without LLM analysis; review the title, abstract, and uploaded manuscript manually.",
        recommended_decision=recommended,
        recommendation_rationale="Heuristic recommendation based on reviewer recommendations because LLM analysis was unavailable.",
        reviewer_consensus_summary=f"Reviewer recommendations: {', '.join(recommendations) or 'none provided'}.",
        main_concerns=concerns[:6],
        has_author_response=has_author_response,
        statistics_flags=statistics_flags or ["Statistics audit unavailable without LLM analysis."],
        statistical_checks=statistical_checks or [],
        open_science_flags=["Open-science audit unavailable without LLM analysis."],
        previous_round_assessment=[],
        uncertainty="LLM analysis was not available; review manually before using.",
        used_llm=False,
        error=error,
    )


def _heuristic_paragraphs(
    selected_decision: str,
    editor_points: str,
    analysis: DecisionAnalysis,
) -> str:
    concerns = "; ".join(analysis.main_concerns) or "the reviewers raised issues that require editorial consideration"
    paragraphs = [
        f"After considering the reviews, my decision is {selected_decision.lower()}. The central issues are {concerns}.",
    ]
    if editor_points.strip():
        paragraphs.append(f"In addition, I would emphasize the following editorial points: {editor_points.strip()}")
    paragraphs.append("Please treat these points as a concise editorial rationale rather than a full decision letter.")
    return "\n\n".join(paragraphs)


def _majority_decision(recommendations: list[str]) -> str:
    if not recommendations:
        return "Major revision"
    normalized = [_clean_decision(item) for item in recommendations]
    severity = {"Accept": 0, "Minor revision": 1, "Major revision": 2, "Reject": 3}
    average = sum(severity[item] for item in normalized) / len(normalized)
    if average < 0.75:
        return "Accept"
    if average < 1.5:
        return "Minor revision"
    if average < 2.5:
        return "Major revision"
    return "Reject"


def _clean_decision(value: object) -> str:
    text = str(value or "").strip().casefold()
    for option in DECISION_OPTIONS:
        if option.casefold() == text:
            return option
    if "minor" in text:
        return "Minor revision"
    if "major" in text or "revise" in text:
        return "Major revision"
    if "reject" in text or "decline" in text:
        return "Reject"
    if "accept" in text:
        return "Accept"
    return "Major revision"


def _clean_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned: list[str] = []
    for value in values:
        item = str(value).strip()
        if item and item.casefold() not in {existing.casefold() for existing in cleaned}:
            cleaned.append(item[:700])
    return cleaned[:12]


def _merge_flags(primary: list[str], secondary: list[str]) -> list[str]:
    merged: list[str] = []
    for value in [*primary, *secondary]:
        item = str(value).strip()
        if item and item.casefold() not in {existing.casefold() for existing in merged}:
            merged.append(item[:700])
    return merged[:16]
