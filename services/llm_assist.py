"""Optional LLM helpers for query expansion and conservative PDF extraction."""

from __future__ import annotations

import re
from functools import lru_cache

from pydantic import BaseModel, Field

from services.llm_provider import (
    call_llm_json,
    codex_cli_model,
    llm_enabled,
    llm_provider,
    local_llm_model,
)
from services.reviewer_retrieval import ReviewerSearchInput, extract_search_terms

MAX_QUERY_COUNT = 5

METHOD_TERMS = {
    "cohort", "randomized", "trial", "qualitative", "interview", "survey",
    "meta-analysis", "systematic review", "machine learning", "deep learning",
    "regression", "longitudinal", "cross-sectional", "case-control",
    "ethnography", "experiment", "simulation", "mixed methods",
}
CONTEXT_TERMS = {
    "adolescent", "children", "adult", "older adults", "patient", "patients",
    "clinical", "community", "school", "hospital", "primary care", "rural",
    "urban", "low-income", "minority", "women", "pediatric", "public health",
}


class ReviewerSearchProfile(BaseModel):
    queries: list[str] = Field(default_factory=list)
    key_topics: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    populations_or_contexts: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    used_llm: bool = False


class LlmExtractedManuscriptFields(BaseModel):
    journal_name: str = ""
    title: str = ""
    abstract: str = ""
    keywords: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    used_llm: bool = False


def build_reviewer_search_profile(search_input: ReviewerSearchInput) -> ReviewerSearchProfile:
    """Build multiple source-ready queries, using an LLM only when configured."""
    return _build_reviewer_search_profile_cached(
        search_input.title,
        search_input.abstract,
        tuple(search_input.keywords),
        llm_provider(),
        _provider_model_cache_key(),
        llm_enabled(),
    )


@lru_cache(maxsize=64)
def _build_reviewer_search_profile_cached(
    title: str,
    abstract: str,
    keywords: tuple[str, ...],
    provider: str,
    provider_model: str,
    provider_enabled: bool,
) -> ReviewerSearchProfile:
    search_input = ReviewerSearchInput(title=title, abstract=abstract, keywords=list(keywords))
    heuristic_profile = _heuristic_search_profile(search_input)
    if not llm_enabled():
        heuristic_profile.notes.append("LLM query expansion unavailable: no enabled LLM provider.")
        return heuristic_profile

    prompt = (
        "Extract a conservative scholarly search profile for reviewer discovery. "
        "Return JSON only with keys: queries, key_topics, methods, populations_or_contexts. "
        "Create 3-5 concise search queries suitable for scholarly metadata APIs. "
        "Use only concepts present in the manuscript title, abstract, or keywords. "
        "Do not invent acronyms, methods, diseases, populations, or fields."
    )
    data = call_llm_json(
        prompt,
        {
            "title": search_input.title,
            "abstract": search_input.abstract[:6000],
            "keywords": search_input.keywords,
        },
        temperature=0,
    )
    if not data:
        heuristic_profile.notes.append(f"LLM query expansion failed via {llm_provider()}; using heuristic queries.")
        return heuristic_profile

    profile = ReviewerSearchProfile(
        queries=_clean_queries(data.get("queries", []), search_input),
        key_topics=_clean_list(data.get("key_topics", [])),
        methods=_clean_list(data.get("methods", [])),
        populations_or_contexts=_clean_list(data.get("populations_or_contexts", [])),
        used_llm=True,
    )
    if not profile.queries:
        return heuristic_profile
    return profile


def _provider_model_cache_key() -> str:
    provider = llm_provider()
    if provider == "local_cli":
        return local_llm_model()
    if provider == "codex_cli":
        return codex_cli_model()
    return ""


def extract_manuscript_fields_with_llm(text: str) -> LlmExtractedManuscriptFields:
    """Conservatively extract manuscript metadata from PDF text if an LLM is configured."""
    if not llm_enabled() or not text.strip():
        return LlmExtractedManuscriptFields(
            notes=["LLM PDF extraction unavailable: no enabled LLM provider."],
        )

    prompt = (
        "Extract manuscript metadata from PDF text. Return JSON only with keys: "
        "journal_name, title, abstract, keywords, notes. "
        "The journal_name must be the journal the manuscript is submitted to, target journal, "
        "or journal explicitly named as the destination. If it is not explicit, return an empty string. "
        "Do not use publisher names, article types, section headings, running headers, or references as the journal. "
        "Do not guess. Preserve the complete title; do not truncate it. "
        "Keywords must be explicit author keywords only; return [] if not clearly labeled. "
        "All returned values must be plain text only: no HTML, XML, markdown, LaTeX commands, or formatting tags."
    )
    data = call_llm_json(prompt, text[:18000], temperature=0)
    if not data:
        return LlmExtractedManuscriptFields(notes=[f"LLM PDF extraction failed via {llm_provider()} or returned invalid JSON."])

    return LlmExtractedManuscriptFields(
        journal_name=_clean_text(data.get("journal_name", ""), max_length=180),
        title=_clean_text(data.get("title", ""), max_length=500),
        abstract=_clean_text(data.get("abstract", ""), max_length=3500),
        keywords=_clean_list(data.get("keywords", []))[:12],
        notes=_clean_list(data.get("notes", []))[:4],
        used_llm=True,
    )


def _heuristic_search_profile(search_input: ReviewerSearchInput) -> ReviewerSearchProfile:
    terms = extract_search_terms(search_input)
    manuscript_text = " ".join(
        [search_input.title, search_input.abstract, " ".join(search_input.keywords)]
    )
    key_phrases = _key_phrases(manuscript_text)
    methods = _terms_present(manuscript_text, METHOD_TERMS)
    contexts = _terms_present(manuscript_text, CONTEXT_TERMS)
    queries: list[str] = []
    if search_input.title.strip():
        queries.append(search_input.title.strip())
    if search_input.keywords:
        queries.append(" ".join(search_input.keywords[:6]))
    if key_phrases:
        queries.append(" ".join(key_phrases[:4]))
    topic_terms = [term for term in [*search_input.keywords, *terms] if term.strip()]
    if topic_terms and methods:
        queries.append(" ".join([*topic_terms[:3], *methods[:2]]))
    if topic_terms and contexts:
        queries.append(" ".join([*topic_terms[:3], *contexts[:2]]))
    if terms:
        queries.append(" ".join(terms[:8]))
    if search_input.abstract:
        abstract_terms = [term for term in terms if term.lower() not in {k.lower() for k in search_input.keywords}]
        if abstract_terms:
            queries.append(" ".join(abstract_terms[:6]))
    return ReviewerSearchProfile(
        queries=_clean_queries(queries, search_input),
        key_topics=_dedupe([*search_input.keywords, *key_phrases, *terms])[:16],
        methods=methods,
        populations_or_contexts=contexts,
        notes=["Using deterministic abstract-aware search expansion."],
    )


def _clean_queries(values: object, search_input: ReviewerSearchInput) -> list[str]:
    raw_values = values if isinstance(values, list) else []
    manuscript_text = " ".join([search_input.title, search_input.abstract, " ".join(search_input.keywords)]).casefold()
    queries: list[str] = []
    for value in raw_values:
        query = _clean_text(str(value), max_length=220)
        query = _dedupe_query_words(query)
        if len(query) < 4:
            continue
        query_terms = re.findall(r"[A-Za-z][A-Za-z-]{3,}", query.casefold())
        known_terms = [term for term in query_terms if term in manuscript_text]
        if query_terms and len(known_terms) / len(query_terms) < 0.45:
            continue
        if query.casefold() not in {item.casefold() for item in queries}:
            queries.append(query)
        if len(queries) >= MAX_QUERY_COUNT:
            break
    return queries


def _dedupe_query_words(query: str) -> str:
    words = query.split()
    cleaned: list[str] = []
    previous = ""
    for word in words:
        normalized = word.casefold().strip(".,;:")
        if normalized == previous:
            continue
        cleaned.append(word)
        previous = normalized
    return " ".join(cleaned)


def _clean_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned: list[str] = []
    for value in values:
        item = _clean_text(str(value), max_length=120)
        if item and item.casefold() not in {existing.casefold() for existing in cleaned}:
            cleaned.append(item)
    return cleaned


def _key_phrases(text: str) -> list[str]:
    phrases: list[str] = []
    for match in re.finditer(
        r"\b([A-Za-z][A-Za-z-]{3,}(?:\s+[A-Za-z][A-Za-z-]{3,}){1,3})\b",
        text,
    ):
        phrase = match.group(1).lower()
        words = phrase.split()
        if any(word in {"this", "that", "with", "from", "using", "study", "paper", "results"} for word in words):
            continue
        if phrase not in phrases:
            phrases.append(phrase)
        if len(phrases) >= 12:
            break
    return phrases


def _terms_present(text: str, vocabulary: set[str]) -> list[str]:
    text = text.casefold()
    found = []
    for term in sorted(vocabulary):
        pattern = r"(?<![A-Za-z0-9])" + re.escape(term.casefold()) + r"(?![A-Za-z0-9])"
        if re.search(pattern, text):
            found.append(term)
    return found


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        value = value.strip()
        if value and value.casefold() not in {item.casefold() for item in deduped}:
            deduped.append(value)
    return deduped


def _clean_text(value: str, max_length: int) -> str:
    value = re.sub(r"</?[^>]{1,80}>", " ", value or "")
    value = re.sub(r"\b(?:strong|em|span|div|html|body)\b\s*", " ", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip(" .:-\t")[:max_length].strip()
