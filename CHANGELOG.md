# Changelog

## v0.1.0-alpha - 2026-04-27

Initial local-use alpha release of Journal Editorial Assistant.

### Included

- Reviewer Finder with evidence-backed reviewer candidates from scholarly metadata.
- OpenAlex backbone with optional Semantic Scholar, Scopus, Crossref, ORCID, and PubMed enrichment.
- Optional Scopus and Semantic Scholar API key fields in the app.
- Synonym-aware matched-keyword filtering.
- English-publication filtering enabled by default.
- Invitation opener drafting from verified publication evidence.
- Decision Assistant with multiple reviewer comments and recommendations.
- Manuscript and author-response PDF extraction.
- Prior-round decision-record saving/loading for revision tracking.
- LLM-assisted decision synthesis through OpenAI-compatible APIs, custom CLI commands, Codex CLI, or Ollama-style local CLI.
- Lightweight statcheck-style p-value consistency screening.

### Notes

- This is an alpha release intended for local use.
- Citation metrics and contact details depend on third-party metadata coverage.
- Generated text and automated flags require editor verification before use.
