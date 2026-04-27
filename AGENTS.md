# AGENTS.md

## Goal
Build an internal journal editorial assistant.

## Tool functions
1. Reviewer Finder:
- Input title, abstract, keywords.
- Retrieve real potential reviewers from scholarly metadata.
- Never invent reviewer names.
- Show evidence for every reviewer.

2. Invitation Drafter:
- Draft a tailored opening paragraph for each reviewer.
- Use only verified publications as evidence.
- Avoid exaggerated praise.

3. Decision Assistant:
- Input manuscript summary and reviewer comments.
- Summarize reviewer concerns.
- Independently audit claims, methods, and evidence.
- Draft an editorial decision letter.

## Core rules
- Retrieval first, AI judgment second, drafting third.
- Never recommend a reviewer without publication evidence.
- Separate reviewer comments from the tool’s independent assessment.
- Mark uncertainty clearly.
- Keep the code simple and readable.

## Technical preferences
- Use Python.
- Use Streamlit for the first version.
- Use Pydantic for data models.
- Use OpenAlex first for reviewer retrieval.
- Add tests for high-risk failures.
