# Journal Editorial Assistant

A local Streamlit app for journal editors. It helps identify evidence-backed reviewer candidates, draft reviewer invitations, synthesize reviewer comments, audit revision responses across rounds, and draft decision-justification paragraphs.

Current release candidate: `v0.2.0-alpha`

## Features

- Reviewer Finder tab with manuscript details, keyword input, author/institution exclusion lists, and evidence-backed reviewer candidates.
- Decision Assistant tab with manuscript details, manuscript/response PDF uploads, multiple reviewer comments, reviewer recommendations, and editor-selected highlights for decision text.
- Separate service modules for retrieval, citation metrics, conflict checks, drafting, comment parsing, memos, and decision letters.
- OpenAlex-backed reviewer retrieval with normalized evidence from Semantic Scholar, Scopus, Clarivate Reviewer Locator, Crossref, ORCID, and PubMed when available.
- Optional OpenAlex contact email/API key, Scopus API key, Semantic Scholar API key, Clarivate credentials, and Wiley TDM token fields in the app.
- Optional Scopus retrieval and citation enrichment when a Scopus API key is configured.
- Semantic Scholar author enrichment can show profile URLs, known affiliations, aliases, publication counts, citation counts, h-index, and external IDs when available.
- Matched papers are labeled by match type: topic content, method, and population/context.
- Candidate ordering prioritizes recent matching papers, journal articles, more overlapping terms, editorial feedback, Scopus-backed profiles, and recent activity.
- Synonym-aware matched-keyword filtering, including groups such as aging/ageing/older adults and well-being/wellbeing.
- Conditional local feedback learning lets users mark candidates useful/irrelevant for a specific manuscript context, with reasons such as wrong topic, wrong method, or wrong population/context.
- Institution-exclusion suggestions use OpenAlex institution search plus affiliations already seen in the current session.
- English-publication filtering is enabled by default and can be turned off by the user.
- PDF field extraction to prefill manuscript fields before editing.
- Decision Assistant statistical audit with a lightweight statcheck-style p-value consistency scan for APA-style test reports.
- Local decision-record storage for later review rounds, so prior concerns can be loaded and checked against authors' responses.
- Generic journal-name entry. No journal-specific board lists or private defaults are bundled in this public template.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

On macOS, users can also double-click `Start Journal Editorial Assistant.command`.
The launcher creates a local `.venv` on first use, installs requirements once,
opens `http://localhost:8501/`, and reuses the environment on later launches.

## Project Structure

```text
app.py
services/
  candidate_publication_enrichment.py
  citation_metrics.py
  conflict_checking.py
  decision_assistant.py
  decision_records.py
  decision_letter_drafting.py
  editorial_memo_drafting.py
  identity_verification.py
  institution_suggestions.py
  invitation_opener_drafting.py
  journal_editorial_board.py
  llm_assist.py
  llm_provider.py
  paper_match_analysis.py
  pdf_extraction.py
  reviewer_comment_parsing.py
  reviewer_retrieval.py
  search_feedback.py
  source_clients.py
  statistical_checking.py
scripts/
  test_scopus_author.py
requirements.txt
AGENTS.md
LICENSE
```

## Reviewer Sources

OpenAlex is the backbone for reviewer discovery. The app also attempts to collect normalized evidence from:

- Semantic Scholar Academic Graph API
- Scopus Search and Author APIs
- Clarivate Reviewer Locator API
- Crossref REST API
- ORCID public API enrichment
- PubMed / NCBI E-utilities

Each evidence item is labeled by source. Candidates are deduplicated by ORCID, Semantic Scholar author ID, OpenAlex author ID, then normalized name, affiliation, and overlapping paper titles.

## Optional Scopus API Key

Set your Scopus API key before running Streamlit to enable Scopus reviewer evidence, paper citation counts, author citation counts, and h-index where available:

```bash
export SCOPUS_API_KEY=your_key_here
streamlit run app.py
```

You can also paste the Scopus key into the app under **API and LLM settings**.

## Optional Semantic Scholar API Key

The app can query Semantic Scholar for author and paper metadata. It works
without a key when public rate limits allow it, but a key can improve reliability
for author search and citation enrichment:

```bash
export SEMANTIC_SCHOLAR_API_KEY=your_key_here
streamlit run app.py
```

You can also paste the Semantic Scholar key into the app under **API and LLM settings**.

## Other Optional API Settings

The **API and LLM settings** panel also supports:

- OpenAlex contact email and optional OpenAlex API key.
- Clarivate Reviewer Locator credentials by API key, bearer token, or client credentials.
- Wiley TDM client token for future Wiley full-text enrichment by DOI.

Keys entered in the app are used for the running session. Users may optionally
save settings locally outside the project folder at their operating system's
application-support path.

### Scopus Author Diagnostics

To inspect what Scopus returns for a specific author, set `SCOPUS_API_KEY` in
your terminal and run:

```bash
npm run test:scopus-author -- "Author Name" "Affiliation"
```

The diagnostic prints which Scopus endpoints succeeded or failed, response
status codes, returned field paths, and whether h-index or citation fields are
present.

## Optional LLM Assistance

The app works without an LLM. It can use an OpenAI-compatible chat completions endpoint, Codex CLI, or an Ollama-style local CLI conservatively for:

- Expanding manuscript details into multiple scholarly metadata search queries.
- Extracting PDF fields, including target/submitted journal name when explicitly present in the PDF text.
- Regenerating reviewer invitation opener drafts from verified publication evidence.
- Synthesizing reviewer comments and checking whether authors addressed prior-round concerns.

### OpenAI-Compatible API

Use any provider that exposes an OpenAI-compatible `/chat/completions` endpoint. In the app, choose **OpenAI-compatible API**, enter your API key, model, and endpoint URL.

```bash
export OPENAI_API_KEY=your_key_here
export OPENAI_MODEL=gpt-4o-mini
export OPENAI_CHAT_COMPLETIONS_URL=https://api.openai.com/v1/chat/completions
streamlit run app.py
```

The LLM is instructed not to guess journal names, titles, keywords, reviewer evidence, or reviewer contact details.

### LLM Policy Caution

Before enabling LLM assistance with manuscript files, reviewer comments, author
responses, or other confidential editorial material, users should check the
relevant journal, publisher, society, funder, institutional, and platform
policies. Some editorial workflows may restrict or prohibit uploading,
transmitting, or processing unpublished manuscript or peer-review content with
external AI systems. When in doubt, use the non-LLM workflow or a locally
approved model/environment.

### Codex CLI LLM

Choose **Codex CLI** if you have Codex installed and authenticated locally. Enter the executable path or simply `codex` if it is on your `PATH`.

### Ollama / Local CLI LLM

Choose **Ollama / Local CLI** for Ollama-style local models:

```bash
ollama pull llama3.1:8b
streamlit run app.py
```

Then use these settings in the app:

- LLM provider: `Ollama / Local CLI`
- LLM command: `ollama`
- LLM model: `llama3.1:8b`
- Use LLM assistance: checked

The app calls the local command as:

```bash
ollama run llama3.1:8b
```

## Statistical Check

The Decision Assistant includes an automated, statcheck-style screen for
APA-formatted hypothesis tests in uploaded manuscript text. It currently checks
reported p-values against reported `t(df)`, `F(df1, df2)`, `chi-square(df)`,
`z`, and `r(df)` statistics, then shows both summary flags and the exact text
snippets that triggered them. Treat these as inspection prompts: they can catch
inconsistent p-values, but they do not evaluate model choice, design quality,
raw data, Bayesian models, multilevel models, SEM, or robustness checks.

## Decision Records

Use **Save decision record for later rounds** in the Decision Assistant to store
the current output locally. Records are written to `decision_records/` as both
Markdown and JSON. On a later revision round, open **Load prior decision record
for a later review round**, select the earlier record, and the app will pass the
prior reviewer concerns, selected highlights, and decision rationale into the
analysis so it can assess whether the authors addressed each issue.

The `decision_records/` folder is ignored by Git because it may contain
confidential manuscript and review material.

If `ollama` is not on the app's PATH, enter the full path to the executable in **LLM command**.

Citation metrics are approximate because they come from third-party scholarly indexes and may differ across OpenAlex, Semantic Scholar, and Scopus.

## License

This project is released under the MIT License. See [LICENSE](LICENSE).

## Editorial Use Disclaimer

Journal Editorial Assistant is intended to support editorial workflows. It does
not replace editorial judgment, statistical review, legal review, publication
ethics review, conflict-of-interest checks, or journal policy decisions. Users
are responsible for verifying reviewer identities, reviewer suitability,
conflicts of interest, citation metrics, statistical flags, and all generated
text before acting on the tool's output.
