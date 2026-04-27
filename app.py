"""Streamlit app for the internal journal editorial assistant."""

from __future__ import annotations

import os
import re
import hashlib
from datetime import date, datetime

import streamlit as st

from services.candidate_publication_enrichment import enrich_candidate_publications
from services.citation_metrics import attach_citation_metrics
from services.conflict_checking import ConflictCheckInput, check_conflicts
from services.decision_assistant import (
    DECISION_OPTIONS,
    DecisionAnalysis,
    ReviewerDecisionInput,
    analyze_decision_materials,
    draft_decision_paragraphs,
)
from services.decision_records import (
    list_decision_records,
    load_record_markdown,
    previous_round_context,
    save_decision_record,
)
from services.identity_verification import attach_identity_verification
from services.invitation_opener_drafting import (
    draft_invitation_opener,
    draft_invitation_opener_with_llm,
    last_invitation_llm_error,
)
from services.journal_editorial_board import (
    editorial_board_lookup_note,
    mark_editorial_board_members,
)
from services.llm_provider import llm_enabled, llm_status_label
from services.llm_assist import build_reviewer_search_profile
from services.pdf_extraction import extract_manuscript_fields_from_pdf, extract_pdf_text
from services.paper_match_analysis import analyze_paper_matches_with_llm
from services.reviewer_retrieval import (
    ReviewerCandidate,
    ReviewerRetrievalError,
    ReviewerSearchInput,
    extract_search_terms,
    retrieve_reviewers,
)
from services.search_feedback import (
    feedback_adjustment,
    feedback_summary,
    record_candidate_feedback,
)

PAGE_SIZE = 10
SOURCE_OPTIONS = ["OpenAlex", "Semantic Scholar", "Scopus", "Crossref", "ORCID", "PubMed"]
FILTER_TERM_SYNONYM_GROUPS = {
    "ageing / aging / older adults": {
        "ageing",
        "aging",
        "aged",
        "age-related",
        "age related",
        "older",
        "older adult",
        "older adults",
        "older people",
        "older person",
        "elder",
        "elders",
        "elderly",
        "senior",
        "seniors",
        "senior adult",
        "senior adults",
        "gerontology",
        "gerontological",
        "late life",
        "later life",
    },
    "adolescents / youth / young people": {
        "adolescent",
        "adolescents",
        "adolescence",
        "teen",
        "teens",
        "teenager",
        "teenagers",
        "youth",
        "young people",
        "young person",
        "young adults",
        "young adult",
    },
    "children / child": {
        "child",
        "children",
        "childhood",
        "kids",
        "pediatric",
        "paediatric",
    },
    "well-being / wellbeing": {
        "wellbeing",
        "well-being",
        "well being",
        "subjective wellbeing",
        "subjective well-being",
        "quality of life",
        "life satisfaction",
    },
    "mental health / psychological distress": {
        "mental health",
        "psychological distress",
        "distress",
        "depression",
        "depressive",
        "anxiety",
        "anxious",
        "stress",
        "psychopathology",
    },
    "cross-cultural / intercultural": {
        "cross cultural",
        "cross-cultural",
        "intercultural",
        "multicultural",
        "culture",
        "cultural",
    },
}
FILTER_TERM_ALIAS_LOOKUP = {
    alias: label
    for label, aliases in FILTER_TERM_SYNONYM_GROUPS.items()
    for alias in aliases
}

st.set_page_config(
    page_title="Journal Editorial Assistant",
    page_icon="JE",
    layout="wide",
)


def render_api_settings() -> None:
    with st.expander("API and LLM settings", expanded=False):
        st.caption("Keys entered here are used for this running app session and are not written to project files.")
        scopus_col, semantic_col = st.columns(2)
        with scopus_col:
            scopus_key = st.text_input(
                "Scopus API key",
                value=st.session_state.get("scopus_api_key", ""),
                type="password",
                help="Enables Scopus reviewer evidence and citation enrichment.",
            )
        with semantic_col:
            semantic_scholar_key = st.text_input(
                "Semantic Scholar API key",
                value=st.session_state.get("semantic_scholar_api_key", ""),
                type="password",
                help="Optional. Improves Semantic Scholar reliability and rate limits for author/citation enrichment.",
            )
        _, provider_col = st.columns(2)
        with provider_col:
            saved_provider = st.session_state.get("llm_provider", "openai")
            provider_options = ["OpenAI-compatible API", "Custom CLI", "Codex CLI", "Ollama / Local CLI"]
            provider_indices = {"openai": 0, "custom_cli": 1, "codex_cli": 2, "local_cli": 3}
            llm_provider_choice = st.selectbox(
                "LLM provider",
                provider_options,
                index=provider_indices.get(saved_provider, 0),
                help="Use an OpenAI-compatible endpoint, a custom local command, Codex CLI, or Ollama-style local CLI.",
            )

        provider_key = {
            "OpenAI-compatible API": "openai",
            "Custom CLI": "custom_cli",
            "Codex CLI": "codex_cli",
            "Ollama / Local CLI": "local_cli",
        }[llm_provider_choice]
        local_col, model_col = st.columns(2)
        with local_col:
            default_command = _default_llm_command(provider_key)
            local_llm_command = st.text_input(
                "LLM command or command template",
                value=st.session_state.get("local_llm_command", default_command),
                disabled=provider_key == "openai",
                help=(
                    "For Custom CLI, enter a command template such as "
                    "`llm --model {model}`. The prompt is passed through stdin. "
                    "For Ollama-style local models, enter `ollama`."
                ),
            )
        with model_col:
            llm_model = st.text_input(
                "LLM model",
                value=st.session_state.get(
                    "llm_model",
                    _default_llm_model(provider_key),
                ),
                help="For Codex CLI, leave blank to use the Codex default model.",
            )

        openai_col, toggle_col = st.columns([2, 1])
        with openai_col:
            openai_key = st.text_input(
                "API key",
                value=st.session_state.get("openai_api_key", ""),
                type="password",
                disabled=provider_key != "openai",
                help="Used only when the provider is OpenAI-compatible API.",
            )
            openai_base_url = st.text_input(
                "OpenAI-compatible chat completions URL",
                value=st.session_state.get("openai_base_url", "https://api.openai.com/v1/chat/completions"),
                disabled=provider_key != "openai",
                help="For compatible providers, enter the full /chat/completions endpoint.",
            )
        with toggle_col:
            use_llm = st.checkbox(
                "Use LLM assistance",
                value=st.session_state.get("use_llm_assistance", False),
            )

        st.session_state["scopus_api_key"] = scopus_key.strip()
        st.session_state["semantic_scholar_api_key"] = semantic_scholar_key.strip()
        st.session_state["llm_provider"] = provider_key
        st.session_state["local_llm_command"] = local_llm_command.strip() or default_command
        st.session_state["llm_model"] = llm_model.strip()
        st.session_state["openai_api_key"] = openai_key.strip()
        st.session_state["openai_base_url"] = openai_base_url.strip()
        st.session_state["use_llm_assistance"] = use_llm
        _apply_api_settings()

        status_parts = [
            "Scopus: enabled" if os.getenv("SCOPUS_API_KEY") else "Scopus: not configured",
            "Semantic Scholar: enabled" if os.getenv("SEMANTIC_SCHOLAR_API_KEY") else "Semantic Scholar: not configured",
            llm_status_label() if use_llm else "LLM: not enabled",
        ]
        st.caption(" | ".join(status_parts))

        if st.button("Clear API keys for this session"):
            for key in ("scopus_api_key", "semantic_scholar_api_key", "openai_api_key"):
                st.session_state[key] = ""
            st.session_state["use_llm_assistance"] = False
            _apply_api_settings()
            st.rerun()


def _apply_api_settings() -> None:
    scopus_key = st.session_state.get("scopus_api_key", "").strip()
    semantic_scholar_key = st.session_state.get("semantic_scholar_api_key", "").strip()
    openai_key = st.session_state.get("openai_api_key", "").strip()
    llm_provider_value = st.session_state.get("llm_provider", "codex_cli").strip()
    llm_model = st.session_state.get("llm_model", "").strip()
    local_llm_command = st.session_state.get("local_llm_command", "").strip()
    openai_base_url = st.session_state.get("openai_base_url", "").strip()
    use_llm = st.session_state.get("use_llm_assistance", False)

    if scopus_key:
        os.environ["SCOPUS_API_KEY"] = scopus_key
    else:
        os.environ.pop("SCOPUS_API_KEY", None)

    if semantic_scholar_key:
        os.environ["SEMANTIC_SCHOLAR_API_KEY"] = semantic_scholar_key
    else:
        os.environ.pop("SEMANTIC_SCHOLAR_API_KEY", None)

    os.environ.pop("OPENAI_API_KEY", None)
    os.environ.pop("OPENAI_MODEL", None)
    os.environ.pop("LLM_PROVIDER", None)
    os.environ.pop("LOCAL_LLM_COMMAND", None)
    os.environ.pop("LOCAL_LLM_COMMAND_TEMPLATE", None)
    os.environ.pop("LOCAL_LLM_MODEL", None)
    os.environ.pop("OPENAI_CHAT_COMPLETIONS_URL", None)
    os.environ.pop("CODEX_CLI_COMMAND", None)
    os.environ.pop("CODEX_CLI_MODEL", None)
    os.environ.pop("CODEX_CLI_HOME", None)

    if use_llm and llm_provider_value == "codex_cli":
        os.environ["LLM_PROVIDER"] = "codex_cli"
        os.environ["CODEX_CLI_COMMAND"] = local_llm_command or "codex"
        os.environ["CODEX_CLI_HOME"] = os.path.expanduser("~/.codex")
        if llm_model:
            os.environ["CODEX_CLI_MODEL"] = llm_model
    elif use_llm and llm_provider_value == "local_cli":
        os.environ["LLM_PROVIDER"] = "local_cli"
        os.environ["LOCAL_LLM_COMMAND"] = local_llm_command or "ollama"
        os.environ["LOCAL_LLM_MODEL"] = llm_model or "llama3.1:8b"
    elif use_llm and llm_provider_value == "custom_cli":
        os.environ["LLM_PROVIDER"] = "custom_cli"
        os.environ["LOCAL_LLM_COMMAND_TEMPLATE"] = local_llm_command
        os.environ["LOCAL_LLM_MODEL"] = llm_model
    elif use_llm and openai_key:
        os.environ["LLM_PROVIDER"] = "openai"
        os.environ["OPENAI_API_KEY"] = openai_key
        os.environ["OPENAI_MODEL"] = llm_model or "gpt-4o-mini"
        if openai_base_url:
            os.environ["OPENAI_CHAT_COMPLETIONS_URL"] = openai_base_url


def _default_llm_command(provider_key: str) -> str:
    if provider_key == "codex_cli":
        return "codex"
    if provider_key == "local_cli":
        return "ollama"
    if provider_key == "custom_cli":
        return "llm --model {model}"
    return ""


def _default_llm_model(provider_key: str) -> str:
    if provider_key == "local_cli":
        return "llama3.1:8b"
    if provider_key == "openai":
        return "gpt-4o-mini"
    return ""


def render_reviewer_finder() -> None:
    st.header("Reviewer Finder")

    _render_pdf_upload()

    with st.form("reviewer_finder_form"):
        journal_name = _journal_selectbox(
            "Journal name",
            key="reviewer_journal_name",
        )
        st.caption("Journal name is optional and can be any journal.")
        title = st.text_input(
            "Title",
            value=st.session_state.get("manuscript_title", ""),
        )
        abstract = st.text_area(
            "Abstract",
            value=st.session_state.get("manuscript_abstract", ""),
            height=180,
        )
        keywords = st.text_input(
            "Keywords",
            value=st.session_state.get("manuscript_keywords", ""),
        )
        excluded_authors = st.text_area(
            "Author names to exclude",
            help="Enter one name per line.",
        )
        excluded_institutions = st.text_area(
            "Institutions to exclude",
            help="Enter one institution per line.",
        )
        exclude_same_institution = st.checkbox(
            "Exclude same institution",
            value=True,
            help="When enabled, candidates whose affiliation matches excluded institutions are suppressed. When disabled, they are flagged instead.",
        )
        prioritize_editorial_board = st.checkbox(
            "Prioritize editorial board members",
            value=True,
            help="Search for the journal editorial board and boost candidates whose names appear there.",
        )
        require_english_publications = st.checkbox(
            "Only include reviewers with English publications",
            value=True,
            help="Keep candidates with at least one retrieved publication marked or inferred as English.",
        )

        submitted = st.form_submit_button("Find reviewers")

    if submitted:
        _run_reviewer_search(
            journal_name=journal_name,
            title=title,
            abstract=abstract,
            keywords=keywords,
            excluded_authors=excluded_authors,
            excluded_institutions=excluded_institutions,
            exclude_same_institution=exclude_same_institution,
            prioritize_editorial_board=prioritize_editorial_board,
            require_english_publications=require_english_publications,
        )
    elif "reviewer_candidates" not in st.session_state:
        st.info("Enter manuscript details to search scholarly sources for reviewer evidence.")
        return

    _render_reviewer_results()


def _render_pdf_upload() -> None:
    uploaded_pdf = st.file_uploader(
        "Upload manuscript PDF to prefill fields",
        type=["pdf"],
        help="Extracted values are editable before you search.",
    )
    if uploaded_pdf is None:
        return

    if st.button("Extract fields from PDF"):
        try:
            extracted = extract_manuscript_fields_from_pdf(uploaded_pdf.getvalue())
        except Exception as exc:  # PDF parsing can fail on malformed or scanned files.
            st.error(f"Could not extract text from PDF: {exc}")
            return

        if extracted.journal_name:
            _set_journal_name(extracted.journal_name)
        if extracted.title:
            st.session_state["manuscript_title"] = extracted.title
        if extracted.abstract:
            st.session_state["manuscript_abstract"] = extracted.abstract
        if extracted.keywords:
            st.session_state["manuscript_keywords"] = ", ".join(extracted.keywords)

        st.session_state["pdf_extraction_notes"] = extracted.notes
        st.session_state["pdf_text_preview"] = extracted.text_preview
        st.session_state["pdf_page_previews"] = extracted.page_previews
        st.success("PDF fields extracted. Review and edit them below before searching.")

    page_previews = st.session_state.get("pdf_page_previews", [])
    preview = st.session_state.get("pdf_text_preview", "")
    if page_previews or preview:
        with st.expander("Extracted PDF text preview"):
            if page_previews:
                for index, page_text in enumerate(page_previews, start=1):
                    st.text_area(
                        f"Page {index}",
                        value=page_text,
                        height=180,
                        disabled=True,
                    )
            else:
                st.text_area("Preview", value=preview, height=220, disabled=True)

    notes = st.session_state.get("pdf_extraction_notes", [])
    if notes:
        with st.expander("PDF extraction notes"):
            for note in notes:
                st.caption(note)

    if page_previews or preview or notes:
        if st.button("Clear uploaded PDF extraction"):
            for key in (
                "pdf_extraction_notes",
                "pdf_text_preview",
                "pdf_page_previews",
            ):
                st.session_state.pop(key, None)
            st.rerun()


def _run_reviewer_search(
    journal_name: str,
    title: str,
    abstract: str,
    keywords: str,
    excluded_authors: str,
    excluded_institutions: str,
    exclude_same_institution: bool,
    prioritize_editorial_board: bool,
    require_english_publications: bool,
) -> None:
    journal_name = _set_journal_name(journal_name)
    st.session_state["manuscript_title"] = title.strip()
    st.session_state["manuscript_abstract"] = abstract.strip()
    st.session_state["manuscript_keywords"] = keywords.strip()

    search_input = ReviewerSearchInput(
        title=title,
        abstract=abstract,
        keywords=[item.strip() for item in keywords.split(",") if item.strip()],
    )
    conflict_input = ConflictCheckInput(
        excluded_author_names=[
            item.strip() for item in excluded_authors.splitlines() if item.strip()
        ],
        excluded_institutions=[
            item.strip() for item in excluded_institutions.splitlines() if item.strip()
        ],
        exclude_same_institution=exclude_same_institution,
    )

    search_terms = extract_search_terms(search_input)
    search_profile = build_reviewer_search_profile(search_input)
    if not search_terms:
        st.warning("Add a title, abstract, or keywords before searching.")
        return

    try:
        candidates = retrieve_reviewers(search_input)
    except ReviewerRetrievalError as exc:
        st.error(str(exc))
        return

    filtered_candidates = check_conflicts(candidates, conflict_input)
    filtered_candidates = enrich_candidate_publications(filtered_candidates, search_input)
    if require_english_publications:
        filtered_candidates = [
            candidate for candidate in filtered_candidates if _has_english_publication(candidate)
        ]
    if prioritize_editorial_board:
        filtered_candidates = mark_editorial_board_members(filtered_candidates, journal_name)
    evidence_candidates = _prepare_reviewer_candidates(filtered_candidates, search_input)

    st.session_state["reviewer_candidates"] = evidence_candidates
    st.session_state["reviewer_search_input"] = search_input
    st.session_state["reviewer_search_terms"] = search_terms
    st.session_state["reviewer_search_profile"] = search_profile
    st.session_state["reviewer_page"] = 0
    st.session_state["enriched_reviewer_names"] = set()
    st.session_state["reviewer_search_completed_message"] = (
        f"Reviewer search complete: found {len(evidence_candidates)} evidence-backed "
        f"candidate{'s' if len(evidence_candidates) != 1 else ''}."
    )
    st.session_state["reviewer_search_completed_at"] = datetime.now().strftime("%H:%M:%S")
    st.toast(st.session_state["reviewer_search_completed_message"], icon="✅")


def _render_reviewer_results() -> None:
    reviewer_candidates: list[ReviewerCandidate] = st.session_state.get(
        "reviewer_candidates",
        [],
    )
    search_input: ReviewerSearchInput | None = st.session_state.get("reviewer_search_input")
    search_terms: list[str] = st.session_state.get("reviewer_search_terms", [])
    search_profile = st.session_state.get("reviewer_search_profile")
    completed_message = st.session_state.get("reviewer_search_completed_message")
    completed_at = st.session_state.get("reviewer_search_completed_at")

    if completed_message:
        timestamp = f" Completed at {completed_at}." if completed_at else ""
        st.success(completed_message + timestamp)

    if search_terms:
        st.caption(f"Search terms: {', '.join(search_terms)}")
    if search_profile:
        with st.expander("Search strategy"):
            st.write("LLM expansion: " + ("enabled" if search_profile.used_llm else "not enabled"))
            if search_profile.queries:
                st.write("Source queries:")
                for query in search_profile.queries:
                    st.caption(query)
            for note in search_profile.notes:
                st.caption(note)

    st.subheader("Reviewer information")
    st.info(
        "This view emphasizes extractable evidence rather than scores: recent papers "
        "with keyword overlap, affiliation, ORCID, title, email, publication count, "
        "and citation count. Scopus is used for publication and citation metrics when "
        "it returns usable author IDs. Email addresses and academic titles still need "
        "public institutional verification."
    )
    st.caption(
        "Editorial board lookup: "
        + editorial_board_lookup_note(st.session_state.get("journal_name", ""))
    )
    _render_feedback_learning_status()
    if not reviewer_candidates or search_input is None:
        st.warning("No evidence-backed reviewer candidates found.")
        return

    filtered_candidates = _render_reviewer_filters(reviewer_candidates)
    if not filtered_candidates:
        st.warning("No candidates match the selected filters.")
        return

    total_candidates = len(filtered_candidates)
    total_pages = max((total_candidates - 1) // PAGE_SIZE + 1, 1)
    page_index = min(st.session_state.get("reviewer_page", 0), total_pages - 1)
    st.session_state["reviewer_page"] = page_index
    _render_pagination_controls(page_index, total_pages, total_candidates, location="top")

    page_start = page_index * PAGE_SIZE
    page_end = min(page_start + PAGE_SIZE, total_candidates)
    page_candidates = _enrich_visible_candidates(filtered_candidates[page_start:page_end])

    st.caption(
        f"Showing candidates {page_start + 1}-{page_end} of {total_candidates}. "
        "Candidates are ordered by number of matching papers first."
    )
    for candidate in page_candidates:
        _render_candidate_card(candidate, search_input)

    _render_pagination_controls(page_index, total_pages, total_candidates, location="bottom")


def _render_pagination_controls(
    page_index: int,
    total_pages: int,
    total_candidates: int,
    location: str,
) -> None:
    previous_col, status_col, next_col = st.columns([1, 2, 1])
    with previous_col:
        previous_clicked = st.button(
            "Previous 10",
            disabled=page_index == 0,
            key=f"previous_reviewers_{location}",
        )
    with status_col:
        st.write(f"Page {page_index + 1} of {total_pages} ({total_candidates} candidates)")
    with next_col:
        next_clicked = st.button(
            "Next 10",
            disabled=page_index >= total_pages - 1,
            key=f"next_reviewers_{location}",
        )

    if previous_clicked:
        st.session_state["reviewer_page"] = max(page_index - 1, 0)
        st.rerun()
    if next_clicked:
        st.session_state["reviewer_page"] = min(page_index + 1, total_pages - 1)
        st.rerun()


def _render_reviewer_filters(
    candidates: list[ReviewerCandidate],
) -> list[ReviewerCandidate]:
    st.markdown("#### Filters")
    keyword_terms = _candidate_filter_options(candidates)
    keyword_counts = _candidate_filter_term_counts(candidates)
    keyword_col, mode_col = st.columns([3, 1])
    with keyword_col:
        selected_keywords = st.multiselect(
            "Filter by matched keywords",
            keyword_terms,
            default=[],
            format_func=lambda term: f"{term} ({keyword_counts.get(_filter_concept_key(term), 0)})",
            help="Choose one or more matched terms from this search pass.",
        )
    with mode_col:
        keyword_match_mode = st.radio(
            "Keyword logic",
            ["Any selected", "All selected"],
            horizontal=False,
            help="Use Any for broad filtering, All for keyword combinations.",
        )

    custom_keywords = st.text_input(
        "Additional keyword filter",
        value="",
        placeholder="Optional: comma-separated terms to filter against matching paper titles",
        help="Useful when you want to test a term that was not automatically extracted.",
    )

    col1, col2, col3 = st.columns([2, 2, 2])
    with col1:
        require_recent_keyword = st.checkbox(
            "Require recent keyword-overlap paper",
            value=False,
            help="Only show candidates with at least one past-10-year paper whose title matches a manuscript keyword/search term.",
        )
    with col2:
        source_filter = st.multiselect(
            "Source coverage filter",
            SOURCE_OPTIONS,
            default=[],
            help="Require candidates to have evidence from every selected source.",
        )
    with col3:
        include_early_career = st.checkbox("Include early-career researchers", value=True)
        require_scopus_metrics = st.checkbox("Require Scopus metrics", value=False)

    filtered: list[ReviewerCandidate] = []
    selected_terms = [
        _filter_concept_key(term)
        for term in [*selected_keywords, *custom_keywords.split(",")]
        if _filter_concept_key(term)
    ]
    for candidate in candidates:
        if source_filter and not all(candidate.source_coverage.get(source, False) for source in source_filter):
            continue
        if selected_terms and not _candidate_matches_selected_terms(
            candidate,
            selected_terms,
            require_all=keyword_match_mode == "All selected",
        ):
            continue
        if require_recent_keyword and not candidate.keyword_match_last_10_years:
            continue
        if require_scopus_metrics and not candidate.scopus_author_id:
            continue
        if _is_early_career_candidate(candidate) and not include_early_career:
            continue
        filtered.append(candidate)

    st.caption(f"{len(filtered)} of {len(candidates)} candidates pass the filters.")
    return filtered


def _candidate_filter_options(candidates: list[ReviewerCandidate]) -> list[str]:
    terms_by_concept: dict[str, str] = {}
    counts = _candidate_filter_term_counts(candidates)
    for candidate in candidates:
        for term in _candidate_all_matched_terms(candidate):
            concept = _filter_concept_key(term)
            if concept and concept not in terms_by_concept:
                terms_by_concept[concept] = _filter_display_label(term)
    return sorted(
        terms_by_concept.values(),
        key=lambda term: (-counts.get(_filter_concept_key(term), 0), term.casefold()),
    )


def _candidate_filter_term_counts(candidates: list[ReviewerCandidate]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in candidates:
        for normalized in {
            _filter_concept_key(term)
            for term in _candidate_all_matched_terms(candidate)
            if _filter_concept_key(term)
        }:
            counts[normalized] = counts.get(normalized, 0) + 1
    return counts


def _candidate_all_matched_terms(candidate: ReviewerCandidate) -> list[str]:
    terms = [*candidate.matched_recent_keywords]
    for paper in candidate.matching_papers:
        terms.extend(paper.matched_keywords)
    return terms


def _candidate_matches_selected_terms(
    candidate: ReviewerCandidate,
    selected_terms: list[str],
    require_all: bool,
) -> bool:
    candidate_terms = {
        _filter_concept_key(term)
        for term in _candidate_all_matched_terms(candidate)
        if _filter_concept_key(term)
    }
    paper_title_text = _normalize_filter_term(" ".join(paper.paper_title for paper in candidate.matching_papers))
    paper_title_concepts = _filter_concepts_in_text(paper_title_text)
    matches = [
        term in candidate_terms or term in paper_title_concepts or term in paper_title_text
        for term in selected_terms
    ]
    return all(matches) if require_all else any(matches)


def _filter_concept_key(term: str) -> str:
    if term in FILTER_TERM_SYNONYM_GROUPS:
        return term
    normalized = _normalize_filter_term(term)
    if not normalized:
        return ""
    return FILTER_TERM_ALIAS_LOOKUP.get(normalized, _singular_filter_term(normalized))


def _filter_display_label(term: str) -> str:
    normalized = _normalize_filter_term(term)
    if normalized in FILTER_TERM_ALIAS_LOOKUP:
        return FILTER_TERM_ALIAS_LOOKUP[normalized]
    return term.strip()


def _filter_concepts_in_text(text: str) -> set[str]:
    normalized = _normalize_filter_term(text)
    concepts: set[str] = set()
    for alias, concept in FILTER_TERM_ALIAS_LOOKUP.items():
        pattern = r"\b" + re.escape(alias).replace(r"\ ", r"\s+") + r"\b"
        if re.search(pattern, normalized):
            concepts.add(concept)
    words = re.findall(r"[a-z][a-z-]{2,}", normalized)
    concepts.update(_singular_filter_term(word) for word in words)
    return concepts


def _singular_filter_term(term: str) -> str:
    if len(term) > 4 and term.endswith("ies"):
        return term[:-3] + "y"
    if len(term) > 4 and term.endswith("s") and not term.endswith("ss"):
        return term[:-1]
    return term


def _normalize_filter_term(term: str) -> str:
    normalized = term.casefold().replace("&", " and ")
    normalized = normalized.replace("ageing", "aging")
    normalized = re.sub(r"[-_/]+", " ", normalized)
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _is_early_career_candidate(candidate: ReviewerCandidate) -> bool:
    years = [
        paper.publication_year
        for paper in candidate.matching_papers
        if paper.publication_year is not None
    ]
    return bool(years and max(years) >= 2019 and len(candidate.matching_papers) <= 3)


def _has_english_publication(candidate: ReviewerCandidate) -> bool:
    return any(_is_english_publication(paper) for paper in candidate.matching_papers)


def _is_english_publication(paper) -> bool:
    language = (paper.publication_language or "").casefold()
    if language in {"en", "eng", "english"}:
        return True
    title = paper.paper_title or ""
    ascii_letters = len(re.findall(r"[A-Za-z]", title))
    non_ascii_letters = len(re.findall(r"[^\W\d_]", title, flags=re.UNICODE)) - ascii_letters
    return ascii_letters >= 8 and ascii_letters >= non_ascii_letters * 3


def _prepare_reviewer_candidates(
    candidates: list[ReviewerCandidate],
    search_input: ReviewerSearchInput,
) -> list[ReviewerCandidate]:
    terms = _keyword_overlap_terms(search_input)
    strict_terms = _strict_paper_match_terms(search_input)
    llm_matches = analyze_paper_matches_with_llm(
        search_input,
        [
            paper
            for candidate in candidates
            for paper in candidate.matching_papers
            if paper.abstract
        ],
    )
    current_year = date.today().year
    for candidate in candidates:
        recent_matches: list[str] = []
        recent_evidence: list[str] = []
        matching_papers: list = []
        for paper in candidate.matching_papers:
            matched = _matched_terms(paper.paper_title, strict_terms)
            if matched:
                paper.match_basis.append("keyword/title-word match")
            llm_match = llm_matches.get(_paper_match_key(paper.paper_title))
            if llm_match and (llm_match.topic_match or llm_match.method_match):
                paper.llm_topic_match = llm_match.topic_match
                paper.llm_method_match = llm_match.method_match
                paper.llm_match_rationale = llm_match.rationale
                paper.match_basis.append("LLM abstract topic/method match")
                for term in llm_match.matched_terms:
                    if term not in matched:
                        matched.append(term)
            if not matched and not paper.match_basis:
                continue
            for term in matched:
                if term not in paper.matched_keywords:
                    paper.matched_keywords.append(term)
            matching_papers.append(paper)
            if paper.publication_year is not None and paper.publication_year >= current_year - 10:
                for term in matched:
                    if term not in recent_matches:
                        recent_matches.append(term)
                recent_evidence.append(
                    f"{paper.publication_year}: {paper.paper_title} "
                    f"(matched: {', '.join(matched)}; source: {paper.source})"
                )
        candidate.matching_papers = matching_papers
        candidate.keyword_match_last_10_years = bool(recent_evidence)
        candidate.matched_recent_keywords = recent_matches
        candidate.recent_keyword_evidence = recent_evidence
        candidate.publication_count = len(candidate.matching_papers)
        candidate.publication_count_source = "Displayed matching publications only"

    candidates = [candidate for candidate in candidates if candidate.matching_papers]
    return sorted(
        candidates,
        key=lambda candidate: (
            len(candidate.matching_papers),
            candidate.is_editorial_board_member,
            feedback_adjustment(candidate, search_input),
            _recent_keyword_overlap_journal_article_count(candidate),
            _recent_keyword_overlap_count(candidate),
            _recent_keyword_overlap_term_count(candidate),
            candidate.scopus_author_id is not None,
            candidate.recent_activity_year or _latest_publication_year(candidate),
        ),
        reverse=True,
    )


def _keyword_overlap_terms(search_input: ReviewerSearchInput) -> list[str]:
    profile = build_reviewer_search_profile(search_input)
    terms = [
        *[term.strip() for term in search_input.keywords if term.strip()],
        *profile.key_topics,
        *profile.methods,
        *profile.populations_or_contexts,
        *extract_search_terms(search_input),
    ]
    deduped: list[str] = []
    for term in terms:
        if term and term.casefold() not in {item.casefold() for item in deduped}:
            deduped.append(term)
    return deduped[:24]


def _strict_paper_match_terms(search_input: ReviewerSearchInput) -> list[str]:
    title_words = [
        word
        for word in re.findall(r"[A-Za-z][A-Za-z-]{4,}", search_input.title.casefold())
        if word not in {
            "among",
            "about",
            "after",
            "based",
            "between",
            "effect",
            "effects",
            "paper",
            "study",
            "using",
            "within",
        }
    ]
    terms = [
        *[term.strip() for term in search_input.keywords if term.strip()],
        *title_words,
    ]
    deduped: list[str] = []
    for term in terms:
        if term and term.casefold() not in {item.casefold() for item in deduped}:
            deduped.append(term)
    return deduped[:24]


def _matched_terms(text: str, terms: list[str]) -> list[str]:
    matches: list[str] = []
    for term in terms:
        term = term.strip()
        if not term:
            continue
        pattern = r"(?<![A-Za-z0-9])" + re.escape(term.casefold()) + r"(?![A-Za-z0-9])"
        if re.search(pattern, text.casefold()):
            matches.append(term)
    return sorted(set(matches), key=str.casefold)


def _paper_match_key(title: str) -> str:
    return " ".join(title.casefold().split())


def _latest_publication_year(candidate: ReviewerCandidate) -> int:
    years = [
        paper.publication_year
        for paper in candidate.matching_papers
        if paper.publication_year is not None
    ]
    return max(years) if years else 0


def _recent_keyword_overlap_count(candidate: ReviewerCandidate) -> int:
    return len(candidate.recent_keyword_evidence)


def _recent_keyword_overlap_term_count(candidate: ReviewerCandidate) -> int:
    return len(candidate.matched_recent_keywords)


def _recent_keyword_overlap_journal_article_count(candidate: ReviewerCandidate) -> int:
    return sum(
        1
        for paper in _recent_keyword_overlap_papers(candidate)
        if _publication_type_priority(paper.publication_type) >= 3
    )


def _enrich_visible_candidates(
    candidates: list[ReviewerCandidate],
) -> list[ReviewerCandidate]:
    enriched_names: set[str] = st.session_state.setdefault("enriched_reviewer_names", set())
    pending_candidates = [
        candidate for candidate in candidates if candidate.name not in enriched_names
    ]
    if not pending_candidates:
        return candidates

    with st.spinner("Loading citation metrics for this page..."):
        pending_candidates = attach_citation_metrics(pending_candidates)

    enriched_by_name = {candidate.name: candidate for candidate in pending_candidates}
    enriched_names.update(enriched_by_name)
    st.session_state["enriched_reviewer_names"] = enriched_names
    return [enriched_by_name.get(candidate.name, candidate) for candidate in candidates]


def _render_candidate_card(
    candidate: ReviewerCandidate,
    search_input: ReviewerSearchInput,
) -> None:
    with st.container(border=True):
        summary_col, metric_col = st.columns([3, 1])
        recent_papers = _recent_keyword_overlap_papers(candidate)
        snippet_papers = recent_papers[:3] or candidate.matching_papers[:3]

        with summary_col:
            st.subheader(candidate.name)
            st.caption(candidate.affiliation or "Affiliation not available")
            if candidate.is_editorial_board_member:
                st.success("Editorial board match")
            elif candidate.editorial_board_status != "Not checked":
                st.caption(f"Editorial board: {candidate.editorial_board_status}")
            st.markdown("**Matching papers**")
            if snippet_papers:
                for paper in snippet_papers:
                    _render_paper_snippet(paper)
            else:
                st.caption("No matching paper evidence available.")

        with metric_col:
            publication_count = (
                f"{candidate.publication_count:,}"
                if candidate.publication_count is not None
                else "Unavailable"
            )
            total_citations = (
                f"{candidate.total_citation_count:,}"
                if candidate.total_citation_count is not None
                else "Unavailable"
            )
            st.metric("Publications", publication_count)
            st.metric("Citations", total_citations)

        with st.expander("Review details"):
            detail_left, detail_right = st.columns([2, 1])
            with detail_left:
                st.write(f"Position/title: {candidate.position_title or candidate.verified_title or 'Unavailable'}")
                title_notes = [
                    note
                    for note in [candidate.position_title_status, candidate.title_status]
                    if note and note != "Not checked"
                ]
                if title_notes:
                    st.caption(" | ".join(title_notes))
                st.write(f"Email: {candidate.email or 'Unavailable'}")
                st.caption(candidate.email_status)
                st.caption(candidate.contact_status)
                if candidate.official_profile_url:
                    st.markdown(f"[Official profile]({candidate.official_profile_url})")
                elif candidate.identity_verification_url:
                    st.markdown(
                        f"[Identity verification source]({candidate.identity_verification_url})"
                    )
                if candidate.is_editorial_board_member and candidate.editorial_board_source:
                    st.markdown(f"[Board evidence]({candidate.editorial_board_source})")
                if st.button("Find affiliation/title/email", key=f"contact_{_candidate_key(candidate)}"):
                    with st.spinner("Searching official profile pages for this reviewer..."):
                        attach_identity_verification([candidate])
                    st.rerun()
            with detail_right:
                h_index = f"{candidate.h_index:,}" if candidate.h_index is not None else "Unavailable"
                matched_citations = (
                    f"{candidate.matched_paper_citation_count:,}"
                    if candidate.matched_paper_citation_count is not None
                    else "Unavailable"
                )
                recent_year = (
                    str(candidate.recent_activity_year)
                    if candidate.recent_activity_year is not None
                    else "Unavailable"
                )
                st.write(f"ORCID: {'Available' if candidate.orcid else 'Unavailable'}")
                st.write(f"Approx. h-index: {h_index}")
                st.write(f"Approx. matched-paper citations: {matched_citations}")
                st.write(f"Recent activity: {recent_year}")

            with st.expander("Metric evidence"):
                st.caption(candidate.citation_metrics_status)
                st.write(f"Publications: {candidate.publication_count_source}")
                st.write(f"Total citations: {candidate.total_citation_source}")
                st.write(f"h-index: {candidate.h_index_source}")
                st.write(
                    "Matched-paper citations: "
                    f"{candidate.matched_paper_citation_source}"
                )
                st.write("Recent activity: latest year among displayed matching papers.")

            covered_sources = [
                source for source, covered in candidate.source_coverage.items() if covered
            ]
            _render_orcid(candidate.orcid)
            st.write("Source coverage: " + (", ".join(covered_sources) or "Unavailable"))
            if candidate.source_ids:
                with st.expander("Source IDs"):
                    for source, values in candidate.source_ids.items():
                        if values:
                            st.write(f"{source}: {', '.join(values[:6])}")
            st.write(candidate.evidence_summary)
            if candidate.conflict_flags:
                st.warning("Possible conflict flags: " + "; ".join(candidate.conflict_flags))
            else:
                st.caption("Possible conflict flags: none from entered exclusions.")
            st.caption(
                "Verification gate passed: name and publications came from scholarly "
                "metadata, and evidence is displayed below."
            )

            st.markdown("**Recent keyword-overlap papers (past 10 years)**")
            if recent_papers:
                st.caption("Matched keywords/search terms: " + ", ".join(candidate.matched_recent_keywords))
                for paper in recent_papers:
                    _render_paper_evidence(paper)
            else:
                st.caption("No past-10-year paper title matched the manuscript keywords/search terms.")

            with st.expander("Other verified publication evidence"):
                for paper in candidate.matching_papers:
                    if paper in recent_papers:
                        continue
                    _render_paper_evidence(paper)

        with st.expander("Invitation opener draft"):
            opener_key = f"llm_opener_{_candidate_key(candidate)}"
            default_opener = draft_invitation_opener(
                candidate,
                search_input,
                st.session_state.get("journal_name", ""),
            )
            st.write(st.session_state.get(opener_key, default_opener))
            if st.button(
                "Regenerate with LLM",
                key=f"regenerate_opener_{_candidate_key(candidate)}",
                disabled=not llm_enabled(),
                help="Uses only verified publication evidence shown for this reviewer.",
            ):
                with st.spinner("Regenerating invitation opener from verified evidence..."):
                    llm_opener = draft_invitation_opener_with_llm(
                        candidate,
                        search_input,
                        st.session_state.get("journal_name", ""),
                    )
                if llm_opener:
                    st.session_state[opener_key] = llm_opener
                    st.toast("LLM invitation opener regenerated.", icon="✅")
                    st.rerun()
                else:
                    error_detail = last_invitation_llm_error()
                    st.warning(
                        "Could not regenerate with LLM. "
                        + (f"Reason: {error_detail}" if error_detail else "No diagnostic reason was returned.")
                    )
            if not llm_enabled():
                st.caption(
                    "LLM regeneration is disabled until LLM assistance is enabled "
                    "with an OpenAI-compatible API, custom CLI, Codex CLI, or local CLI in settings."
                )
        with st.expander("Teach the search"):
            _render_candidate_feedback(candidate, search_input)


def _candidate_key(candidate: ReviewerCandidate) -> str:
    raw = candidate.orcid or candidate.scopus_author_id or candidate.source_openalex_author_id or candidate.name
    return re.sub(r"[^A-Za-z0-9]+", "_", raw)[:80]


def _render_feedback_learning_status() -> None:
    summary = feedback_summary()
    if not summary["total"]:
        st.caption(
            "Learning feedback: no saved feedback yet. Mark candidates useful or "
            "irrelevant to tune future result ordering."
        )
        return
    st.caption(
        "Learning feedback: "
        f"{summary['useful']} useful, {summary['irrelevant']} irrelevant "
        f"({summary['total']} total). Future searches use this to adjust ordering."
    )


def _render_candidate_feedback(
    candidate: ReviewerCandidate,
    search_input: ReviewerSearchInput,
) -> None:
    st.caption(
        "This stores local feedback and adjusts future result ordering for the "
        "same reviewer or similar topic terms. It does not remove publication evidence."
    )
    feedback_key = _candidate_key(candidate)
    with st.form(f"feedback_{feedback_key}"):
        label = st.radio(
            "How relevant is this candidate?",
            ["useful", "irrelevant"],
            horizontal=True,
            key=f"feedback_label_{feedback_key}",
        )
        note = st.text_input(
            "Optional note",
            placeholder="e.g., wrong method area, too clinical, good regional fit",
            key=f"feedback_note_{feedback_key}",
        )
        submitted = st.form_submit_button("Save feedback")
    if submitted:
        record_candidate_feedback(candidate, search_input, label, note)
        st.toast(f"Feedback saved: {candidate.name} marked {label}.", icon="✅")
        st.rerun()


def _render_paper_snippet(paper) -> None:
    paper_link = paper.url or paper.doi or paper.openalex_url
    year = paper.publication_year or "Year unavailable"
    journal = paper.journal_name or "Journal/source unavailable"
    matched = f" | matched: {', '.join(paper.matched_keywords)}" if paper.matched_keywords else ""
    basis = f" | basis: {', '.join(sorted(set(paper.match_basis)))}" if paper.match_basis else ""
    if paper_link:
        st.markdown(f"- [{paper.paper_title}]({paper_link}) ({year})")
    else:
        st.markdown(f"- {paper.paper_title} ({year})")
    st.caption(f"{journal}{matched}{basis}")


def _render_paper_evidence(paper) -> None:
    paper_link = paper.url or paper.doi or paper.openalex_url
    year = paper.publication_year or "Year unavailable"
    st.markdown(f"- [{paper.paper_title}]({paper_link}) ({year})")
    st.caption(f"Journal/source: {paper.journal_name or 'Unavailable'}")
    st.caption(f"Publication type: {paper.publication_type or 'Unavailable'}")
    st.caption(f"Language: {paper.publication_language or 'Unavailable'}")
    st.caption(f"Source: {paper.source}")
    paper_citations = (
        f"{paper.citation_count:,}"
        if paper.citation_count is not None
        else "Unavailable"
    )
    st.caption(f"Approx. citations for this matched paper: {paper_citations}")
    if paper.matched_keywords:
        st.caption("Matched terms: " + ", ".join(paper.matched_keywords))
    if paper.match_basis:
        st.caption("Match basis: " + ", ".join(sorted(set(paper.match_basis))))
    if paper.llm_match_rationale:
        st.caption(f"LLM abstract rationale: {paper.llm_match_rationale}")
    st.caption(f"Evidence link: {paper_link}")


def _recent_keyword_overlap_papers(candidate: ReviewerCandidate):
    current_year = date.today().year
    papers = [
        paper
        for paper in candidate.matching_papers
        if paper.publication_year is not None
        and paper.publication_year >= current_year - 10
        and paper.matched_keywords
    ]
    return sorted(
        papers,
        key=lambda paper: (
            _publication_type_priority(paper.publication_type),
            len(set(paper.matched_keywords)),
            paper.publication_year or 0,
        ),
        reverse=True,
    )


def _publication_type_priority(publication_type: str | None) -> int:
    normalized = (publication_type or "").casefold()
    if "journal" in normalized or normalized == "article":
        return 3
    if "conference" in normalized or "proceedings" in normalized:
        return 2
    if "chapter" in normalized or "book" in normalized:
        return 1
    return 0


def _render_orcid(orcid: str | None) -> None:
    if not orcid:
        st.write("ORCID: Unavailable")
        return
    clean_orcid = _format_orcid(orcid)
    st.markdown(f"ORCID: [{clean_orcid}](https://orcid.org/{clean_orcid})")


def _format_orcid(orcid: str | None) -> str:
    if not orcid:
        return "Unavailable"
    return orcid.replace("https://orcid.org/", "").strip()


def render_decision_assistant() -> None:
    st.header("Decision Assistant")

    st.caption(
        "Upload available PDFs, enter reviewer recommendations/comments, then generate an "
        "editorial recommendation and decision-justification paragraphs."
    )

    _render_previous_round_loader()

    manuscript_pdf = st.file_uploader(
        "Upload manuscript PDF",
        type=["pdf"],
        key="decision_manuscript_pdf",
    )
    response_pdf = st.file_uploader(
        "Upload authors' response to comments PDF",
        type=["pdf"],
        key="decision_response_pdf",
    )
    upload_cols = st.columns(2)
    with upload_cols[0]:
        if st.button("Extract manuscript PDF fields"):
            _extract_decision_manuscript_pdf(manuscript_pdf)
    with upload_cols[1]:
        if st.button("Extract response PDF text"):
            _extract_decision_response_pdf(response_pdf)
    if st.session_state.get("decision_manuscript_text"):
        with st.expander("Manuscript text preview"):
            st.text_area(
                "Manuscript preview",
                value=st.session_state["decision_manuscript_text"][:3000],
                height=220,
                disabled=True,
            )
    if st.session_state.get("decision_response_text"):
        with st.expander("Authors' response text preview"):
            st.text_area(
                "Response preview",
                value=st.session_state["decision_response_text"][:3000],
                height=220,
                disabled=True,
            )

    reviewer_count = st.number_input(
        "Number of reviewers",
        min_value=1,
        max_value=8,
        value=int(st.session_state.get("decision_reviewer_count", 2)),
        step=1,
    )
    st.session_state["decision_reviewer_count"] = int(reviewer_count)

    with st.form("decision_assistant_form"):
        journal_name = _journal_selectbox("Journal name", key="decision_journal_name")
        manuscript_title = st.text_input(
            "Manuscript title",
            value=st.session_state.get("manuscript_title", ""),
        )
        abstract = st.text_area(
            "Abstract",
            value=st.session_state.get("manuscript_abstract", ""),
            height=150,
        )

        reviewer_inputs: list[ReviewerDecisionInput] = []
        for index in range(int(reviewer_count)):
            st.markdown(f"**Reviewer {index + 1}**")
            recommendation = st.selectbox(
                "Reviewer recommendation",
                DECISION_OPTIONS,
                index=2,
                key=f"reviewer_recommendation_{index}",
            )
            comments = st.text_area(
                "Reviewer comments",
                value=st.session_state.get(f"reviewer_comments_{index}", ""),
                height=180,
                key=f"reviewer_comments_{index}",
            )
            reviewer_inputs.append(
                ReviewerDecisionInput(
                    reviewer_label=f"Reviewer {index + 1}",
                    recommendation=recommendation,
                    comments=comments,
                )
            )

        submitted = st.form_submit_button("Analyze decision materials")

    if submitted:
        journal_name = _set_journal_name(journal_name)
        st.session_state["manuscript_title"] = manuscript_title.strip()
        st.session_state["manuscript_abstract"] = abstract.strip()
        with st.spinner("Analyzing decision materials..."):
            analysis = analyze_decision_materials(
                journal_name=journal_name,
                manuscript_title=manuscript_title,
                abstract=abstract,
                manuscript_text=st.session_state.get("decision_manuscript_text", ""),
                author_response_text=st.session_state.get("decision_response_text", ""),
                reviewer_inputs=reviewer_inputs,
                previous_decision_record_text=st.session_state.get("previous_decision_record_context", ""),
            )
        st.session_state["decision_analysis"] = analysis
        st.session_state["decision_reviewer_inputs"] = reviewer_inputs
        st.session_state.pop("decision_paragraphs", None)

    analysis = st.session_state.get("decision_analysis")
    reviewer_inputs = st.session_state.get("decision_reviewer_inputs", [])
    if not analysis:
        st.info("Enter reviewer comments and recommendations to analyze decision materials.")
        return

    _render_decision_analysis(analysis)
    _render_decision_paragraph_builder(
        journal_name=st.session_state.get("journal_name", ""),
        manuscript_title=st.session_state.get("manuscript_title", ""),
        analysis=analysis,
        reviewer_inputs=reviewer_inputs,
    )
    _render_decision_exports(
        journal_name=st.session_state.get("journal_name", ""),
        manuscript_title=st.session_state.get("manuscript_title", ""),
        analysis=analysis,
        reviewer_inputs=reviewer_inputs,
    )


def _extract_decision_manuscript_pdf(uploaded_pdf) -> None:
    if uploaded_pdf is None:
        st.warning("Upload a manuscript PDF first.")
        return
    try:
        pdf_bytes = uploaded_pdf.getvalue()
        extracted = extract_manuscript_fields_from_pdf(pdf_bytes)
        st.session_state["decision_manuscript_text"] = extract_pdf_text(pdf_bytes)
    except Exception as exc:
        st.error(f"Could not extract manuscript PDF: {exc}")
        return
    if extracted.journal_name:
        _set_journal_name(extracted.journal_name)
    if extracted.title:
        st.session_state["manuscript_title"] = extracted.title
    if extracted.abstract:
        st.session_state["manuscript_abstract"] = extracted.abstract
    if extracted.keywords:
        st.session_state["manuscript_keywords"] = ", ".join(extracted.keywords)
    st.success("Manuscript PDF extracted. Review the editable fields below.")
    if extracted.notes:
        with st.expander("Manuscript PDF extraction notes"):
            for note in extracted.notes:
                st.caption(note)


def _extract_decision_response_pdf(uploaded_pdf) -> None:
    if uploaded_pdf is None:
        st.warning("Upload an authors' response PDF first.")
        return
    try:
        response_text = extract_pdf_text(uploaded_pdf.getvalue())
    except Exception as exc:
        st.error(f"Could not extract response PDF: {exc}")
        return
    st.session_state["decision_response_text"] = response_text
    st.success("Authors' response text extracted.")


def _render_previous_round_loader() -> None:
    records = list_decision_records()
    with st.expander("Load prior decision record for a later review round", expanded=False):
        if not records:
            st.caption("No saved decision records yet. Save the current Decision Assistant output to create one.")
            return
        labels = [
            (
                f"{record.get('saved_at', 'unknown date')} | "
                f"{record.get('manuscript_title', 'Untitled manuscript')} | "
                f"{record.get('selected_decision', 'No decision')}"
            )
            for record in records
        ]
        selected_index = st.selectbox(
            "Prior decision record",
            range(len(records)),
            format_func=lambda index: labels[index],
            key="selected_prior_decision_record_index",
        )
        selected_record = records[int(selected_index)]
        load_cols = st.columns(2)
        with load_cols[0]:
            if st.button("Use this prior record"):
                st.session_state["previous_decision_record_context"] = previous_round_context(selected_record)
                st.session_state["previous_decision_record_label"] = labels[int(selected_index)]
                st.success("Prior decision record loaded for this review round.")
        with load_cols[1]:
            if st.button("Clear prior record"):
                st.session_state.pop("previous_decision_record_context", None)
                st.session_state.pop("previous_decision_record_label", None)
                st.success("Prior decision record cleared.")
        if st.session_state.get("previous_decision_record_label"):
            st.info(f"Using prior record: {st.session_state['previous_decision_record_label']}")
        if st.checkbox("Show prior record preview", key="show_prior_record_preview"):
            st.text_area(
                "Saved record",
                value=load_record_markdown(selected_record)[:5000],
                height=260,
                disabled=True,
            )


def _render_decision_analysis(analysis: DecisionAnalysis) -> None:
    st.subheader("Submitted Paper Summary")
    st.write(analysis.manuscript_summary or "No manuscript summary returned.")

    st.subheader("Editorial Recommendation")
    if analysis.used_llm:
        st.success(f"LLM recommended decision: {analysis.recommended_decision}")
    else:
        st.warning(f"Heuristic recommended decision: {analysis.recommended_decision}")
        if analysis.error:
            st.caption(f"Reason LLM was unavailable: {analysis.error}")
    st.write(analysis.recommendation_rationale or "No rationale returned.")

    if not analysis.has_author_response:
        st.info("No authors' response was attached, so this is treated as a first-round decision.")

    cols = st.columns(2)
    with cols[0]:
        st.markdown("**Detailed reviewer-comment summary**")
        st.write(analysis.reviewer_consensus_summary or "Not available.")
        if analysis.reviewer_convergence:
            st.markdown("**Reviewer convergence**")
            _render_highlightable_points("reviewer_convergence", analysis.reviewer_convergence)
        if analysis.reviewer_divergence:
            st.markdown("**Reviewer divergence**")
            for item in analysis.reviewer_divergence:
                st.markdown(f"- {item}")
        st.markdown("**Main concerns**")
        if analysis.main_concerns:
            _render_highlightable_points("main_concerns", analysis.main_concerns)
        else:
            st.caption("No concerns returned.")
    with cols[1]:
        st.markdown("**Strengths**")
        if analysis.strengths:
            for strength in analysis.strengths:
                st.markdown(f"- {strength}")
        else:
            st.caption("No strengths returned.")
        if analysis.has_author_response:
            st.markdown("**Author response assessment**")
            st.write(analysis.author_response_assessment or "No author response assessment returned.")
        if analysis.previous_round_assessment:
            st.markdown("**Prior-round issues addressed**")
            for point in analysis.previous_round_assessment:
                st.markdown(f"- {point}")

    audit_cols = st.columns(2)
    with audit_cols[0]:
        st.markdown("**Potential statistics issues**")
        if analysis.statistics_flags:
            _render_highlightable_points("statistics_flags", analysis.statistics_flags)
        else:
            st.caption("No statistics flags returned.")
        if analysis.statistical_checks:
            with st.expander("Automated p-value consistency details"):
                for index, check in enumerate(analysis.statistical_checks, start=1):
                    status = str(check.get("status", "unknown")).title()
                    computed_p = check.get("computed_p")
                    computed_text = _format_export_p(computed_p) if isinstance(computed_p, (int, float)) else "Unavailable"
                    dfs = check.get("dfs", [])
                    df_text = ", ".join(f"{float(df):g}" for df in dfs) if isinstance(dfs, list) else ""
                    st.markdown(
                        f"**{index}. {status}: {check.get('test_type', 'test')} "
                        f"{f'({df_text})' if df_text else ''}**"
                    )
                    st.caption(
                        f"Reported {check.get('reported_p', 'p unavailable')} | "
                        f"Computed p approximately {computed_text}"
                    )
                    st.write(check.get("message", "No check message available."))
                    if check.get("snippet"):
                        st.code(str(check["snippet"]), language="text")
    with audit_cols[1]:
        st.markdown("**Open science practices**")
        if analysis.open_science_flags:
            _render_highlightable_points("open_science_flags", analysis.open_science_flags)
        else:
            st.caption("No open-science flags returned.")

    if analysis.editor_attention_points:
        with st.expander("Editor attention points"):
            _render_highlightable_points("editor_attention_points", analysis.editor_attention_points)
    if analysis.uncertainty:
        st.caption(f"Uncertainty: {analysis.uncertainty}")


def _render_highlightable_points(section_key: str, points: list[str]) -> None:
    for index, point in enumerate(points):
        point_col, check_col = st.columns([5, 1])
        with point_col:
            st.markdown(f"- {point}")
        with check_col:
            st.checkbox(
                "Highlight",
                key=_highlight_checkbox_key(section_key, index, point),
                help="Include this point when drafting the decision paragraphs.",
            )


def _highlight_checkbox_key(section_key: str, index: int, point: str) -> str:
    digest = hashlib.sha1(point.encode("utf-8")).hexdigest()[:10]
    return f"highlight_{section_key}_{index}_{digest}"


def _selected_letter_highlights(analysis: DecisionAnalysis) -> dict[str, list[str]]:
    sections = {
        "main_concerns": analysis.main_concerns,
        "reviewer_convergence": analysis.reviewer_convergence,
        "statistics_flags": analysis.statistics_flags,
        "open_science_flags": analysis.open_science_flags,
        "editor_attention_points": analysis.editor_attention_points,
    }
    selected: dict[str, list[str]] = {}
    for section_key, points in sections.items():
        checked = [
            point
            for index, point in enumerate(points)
            if st.session_state.get(_highlight_checkbox_key(section_key, index, point))
        ]
        if checked:
            selected[section_key] = checked
    return selected


def _selected_letter_highlights_text(analysis: DecisionAnalysis) -> str:
    selected = _selected_letter_highlights(analysis)
    if not selected:
        return ""
    labels = {
        "main_concerns": "Main concerns selected for the letter",
        "reviewer_convergence": "Reviewer convergence selected for the letter",
        "statistics_flags": "Statistical issues selected for the letter",
        "open_science_flags": "Open science points selected for the letter",
        "editor_attention_points": "Editor attention points selected for the letter",
    }
    lines: list[str] = []
    for section_key, points in selected.items():
        lines.append(labels.get(section_key, section_key))
        lines.extend(f"- {point}" for point in points)
    return "\n".join(lines)


def _render_decision_paragraph_builder(
    journal_name: str,
    manuscript_title: str,
    analysis: DecisionAnalysis,
    reviewer_inputs: list[ReviewerDecisionInput],
) -> None:
    st.subheader("Decision Paragraph Builder")
    selected_decision = st.selectbox(
        "Your decision",
        DECISION_OPTIONS,
        index=DECISION_OPTIONS.index(analysis.recommended_decision)
        if analysis.recommended_decision in DECISION_OPTIONS
        else 2,
        key="editor_selected_decision",
    )
    editor_points = st.text_area(
        "Your own editorial points to integrate",
        value=st.session_state.get("editor_decision_points", ""),
        height=140,
        placeholder="Add any issues you want emphasized, softened, or clarified.",
    )
    st.session_state["editor_decision_points"] = editor_points
    selected_highlights_text = _selected_letter_highlights_text(analysis)
    if selected_highlights_text:
        with st.expander("Selected points to highlight in the letter", expanded=True):
            st.markdown(selected_highlights_text)

    if st.button("Draft decision-justification paragraphs"):
        combined_editor_points = _combine_editor_points(editor_points, selected_highlights_text)
        with st.spinner("Drafting paragraphs..."):
            paragraphs = draft_decision_paragraphs(
                journal_name=journal_name,
                manuscript_title=manuscript_title,
                selected_decision=selected_decision,
                editor_points=combined_editor_points,
                analysis=analysis,
                reviewer_inputs=reviewer_inputs,
            )
        st.session_state["decision_paragraphs"] = paragraphs

    if st.session_state.get("decision_paragraphs"):
        st.markdown("**Draft paragraphs**")
        st.write(st.session_state["decision_paragraphs"])


def _render_decision_exports(
    journal_name: str,
    manuscript_title: str,
    analysis: DecisionAnalysis,
    reviewer_inputs: list[ReviewerDecisionInput],
) -> None:
    st.subheader("Export Record")
    export_markdown = _decision_export_markdown(
        journal_name=journal_name,
        manuscript_title=manuscript_title,
        analysis=analysis,
        reviewer_inputs=reviewer_inputs,
        selected_decision=st.session_state.get("editor_selected_decision", analysis.recommended_decision),
        editor_points=st.session_state.get("editor_decision_points", ""),
        selected_highlights=_selected_letter_highlights(analysis),
        decision_paragraphs=st.session_state.get("decision_paragraphs", ""),
    )
    file_stem = _safe_export_filename(manuscript_title or "decision-record")
    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "Download Markdown",
            data=export_markdown,
            file_name=f"{file_stem}.md",
            mime="text/markdown",
        )
    with col2:
        st.download_button(
            "Download plain text",
            data=_markdown_to_plain_text(export_markdown),
            file_name=f"{file_stem}.txt",
            mime="text/plain",
        )
    if st.button("Save decision record for later rounds"):
        saved = save_decision_record(
            journal_name=journal_name,
            manuscript_title=manuscript_title,
            selected_decision=st.session_state.get("editor_selected_decision", analysis.recommended_decision),
            editor_points=st.session_state.get("editor_decision_points", ""),
            selected_highlights=_selected_letter_highlights(analysis),
            analysis=analysis.model_dump(),
            reviewers=[reviewer.model_dump() for reviewer in reviewer_inputs],
            markdown=export_markdown,
        )
        st.success(f"Saved decision record: {saved['markdown_path']}")


def _decision_export_markdown(
    journal_name: str,
    manuscript_title: str,
    analysis: DecisionAnalysis,
    reviewer_inputs: list[ReviewerDecisionInput],
    selected_decision: str,
    editor_points: str,
    selected_highlights: dict[str, list[str]],
    decision_paragraphs: str,
) -> str:
    sections = [
        "# Editorial Decision Record",
        f"**Journal:** {journal_name or 'Not specified'}",
        f"**Manuscript title:** {manuscript_title or 'Untitled manuscript'}",
        f"**Exported:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Submitted Paper Summary",
        analysis.manuscript_summary or "No manuscript summary returned.",
        "",
        "## Recommendation",
        f"**LLM/heuristic recommendation:** {analysis.recommended_decision}",
        analysis.recommendation_rationale or "No rationale returned.",
        f"**Editor-selected decision:** {selected_decision}",
        "",
        "## Reviewer Synthesis",
        analysis.reviewer_consensus_summary or "Not available.",
        "",
        "### Reviewer Convergence",
        _markdown_list(analysis.reviewer_convergence),
        "",
        "### Reviewer Divergence",
        _markdown_list(analysis.reviewer_divergence),
        "",
        "### Main Concerns",
        _markdown_list(analysis.main_concerns),
        "",
        "### Strengths",
        _markdown_list(analysis.strengths),
        "",
        "### Prior-Round Issues Addressed",
        _markdown_list(analysis.previous_round_assessment),
        "",
    ]
    if analysis.has_author_response:
        sections.extend([
            "## Author Response Assessment",
            analysis.author_response_assessment or "No author response assessment returned.",
            "",
        ])
    else:
        sections.extend([
            "## Review Round",
            "No authors' response was attached; treated as a first-round decision.",
            "",
        ])
    sections.extend([
        "## Potential Statistics Issues",
        _markdown_list(analysis.statistics_flags),
        "",
        "### Automated P-Value Consistency Details",
        _statistical_checks_markdown(analysis.statistical_checks),
        "",
        "## Open Science Practices",
        _markdown_list(analysis.open_science_flags),
        "",
        "## Editor Attention Points",
        _markdown_list(analysis.editor_attention_points),
        "",
        "## Selected Points To Highlight In Letter",
        _selected_highlights_markdown(selected_highlights),
        "",
        "## Editor's Own Points",
        editor_points or "No additional editor points entered.",
        "",
        "## Draft Decision-Justification Paragraphs",
        decision_paragraphs or "No decision paragraphs drafted yet.",
        "",
        "## Raw Reviewer Inputs",
    ])
    for reviewer in reviewer_inputs:
        sections.extend([
            f"### {reviewer.reviewer_label}",
            f"**Recommendation:** {reviewer.recommendation}",
            "",
            reviewer.comments or "No comments entered.",
            "",
        ])
    if analysis.uncertainty:
        sections.extend(["## Uncertainty", analysis.uncertainty, ""])
    if analysis.error:
        sections.extend(["## LLM/Analysis Error", analysis.error, ""])
    return "\n".join(sections).strip() + "\n"


def _markdown_list(items: list[str]) -> str:
    if not items:
        return "- None returned."
    return "\n".join(f"- {item}" for item in items)


def _markdown_to_plain_text(markdown: str) -> str:
    text = re.sub(r"^#+\s*", "", markdown, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    return text


def _combine_editor_points(editor_points: str, selected_highlights_text: str) -> str:
    if not selected_highlights_text:
        return editor_points
    parts = []
    if editor_points.strip():
        parts.append(editor_points.strip())
    parts.append("Please emphasize these checked points in the decision paragraphs:\n" + selected_highlights_text)
    return "\n\n".join(parts)


def _selected_highlights_markdown(selected_highlights: dict[str, list[str]]) -> str:
    if not selected_highlights:
        return "No points selected for emphasis."
    labels = {
        "main_concerns": "Main concerns",
        "reviewer_convergence": "Reviewer convergence",
        "statistics_flags": "Potential statistics issues",
        "open_science_flags": "Open science practices",
        "editor_attention_points": "Editor attention points",
    }
    lines: list[str] = []
    for section_key, points in selected_highlights.items():
        lines.append(f"### {labels.get(section_key, section_key)}")
        lines.append(_markdown_list(points))
        lines.append("")
    return "\n".join(lines).strip()


def _statistical_checks_markdown(checks: list[dict[str, object]]) -> str:
    if not checks:
        return "No automated p-value consistency details available."
    lines: list[str] = []
    for index, check in enumerate(checks, start=1):
        computed_p = check.get("computed_p")
        computed_text = _format_export_p(computed_p) if isinstance(computed_p, (int, float)) else "Unavailable"
        dfs = check.get("dfs", [])
        df_text = ", ".join(f"{float(df):g}" for df in dfs) if isinstance(dfs, list) else ""
        label = f"{check.get('test_type', 'test')}({df_text})" if df_text else str(check.get("test_type", "test"))
        lines.append(
            f"{index}. **{str(check.get('status', 'unknown')).title()}** - "
            f"{label}, reported {check.get('reported_p', 'p unavailable')}, computed p approx. {computed_text}."
        )
        if check.get("message"):
            lines.append(f"   - {check['message']}")
        if check.get("snippet"):
            lines.append(f"   - Snippet: {check['snippet']}")
    return "\n".join(lines)


def _format_export_p(value: object) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "Unavailable"
    if numeric < 0.001:
        return "< .001"
    return f"{numeric:.3f}".replace("0.", ".")


def _safe_export_filename(value: str) -> str:
    filename = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().lower())
    filename = filename.strip("-")[:80]
    return filename or "decision-record"


def _journal_selectbox(label: str, key: str) -> str:
    pending_sync_keys: set[str] = st.session_state.setdefault("journal_sync_keys", set())
    selected_journal = st.session_state.get(key, st.session_state.get("journal_name", ""))
    if key in pending_sync_keys:
        selected_journal = st.session_state.get("journal_name", selected_journal)
        st.session_state[key] = selected_journal
        pending_sync_keys.discard(key)
        st.session_state["journal_sync_keys"] = pending_sync_keys
    return st.text_input(
        label,
        value=selected_journal,
        key=key,
    )


def _set_journal_name(journal_name: str) -> str:
    supported_name = journal_name.strip()
    st.session_state["journal_name"] = supported_name
    st.session_state["journal_sync_keys"] = {
        "reviewer_journal_name",
        "decision_journal_name",
    }
    return supported_name


def main() -> None:
    st.title("Journal Editorial Assistant")
    render_api_settings()

    reviewer_tab, decision_tab = st.tabs(["Reviewer Finder", "Decision Assistant"])

    with reviewer_tab:
        render_reviewer_finder()

    with decision_tab:
        render_decision_assistant()


if __name__ == "__main__":
    main()
