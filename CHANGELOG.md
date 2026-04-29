# Changelog

## v0.2.0-alpha - 2026-04-29

Second local-use alpha release of Journal Editorial Assistant.

### Added

- API settings for OpenAlex contact/API key, Clarivate Reviewer Locator credentials, Semantic Scholar, Scopus, and Wiley TDM token.
- Richer Semantic Scholar author enrichment, including profile links, aliases, known affiliations, external IDs, publication counts, citation counts, and h-index when available.
- Reviewer match labels for topic content, method, and population/context on matched papers.
- Institution exclusion suggestions using OpenAlex institution search and affiliations already seen in the current session.
- Conditional feedback learning that treats useful/irrelevant labels as manuscript-specific, with reason tags such as wrong topic, wrong method, and wrong population/context.
- Smarter Reviewer Finder filters, including a safer Scopus author-profile filter that is disabled when no Scopus-backed candidates are available.
- macOS double-click launcher for local startup.

### Changed

- Public template keeps journal name as a free-text field and does not bundle journal-specific editorial-board lists.
- Candidate ordering emphasizes recent matching publications, journal articles, overlapping terms, local feedback, Scopus-backed profiles, and recent activity.
- README now includes an LLM policy caution reminding users to check journal/publisher policy before using LLM features with confidential editorial material.

### Notes

- This remains an alpha release intended for local use.
- Citation metrics, author affiliations, emails, and h-index values depend on third-party metadata coverage and should be verified.

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
