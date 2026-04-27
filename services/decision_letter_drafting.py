"""Decision letter drafting service."""

from __future__ import annotations

from services.reviewer_comment_parsing import ParsedReviewerComments


def draft_decision_letter(
    manuscript_title: str,
    parsed_comments: ParsedReviewerComments,
    editorial_memo: str,
    journal_name: str = "",
) -> str:
    """Draft a decision letter."""
    _ = parsed_comments
    _ = editorial_memo
    title = manuscript_title or "Untitled manuscript"
    journal = journal_name or "the journal"
    return f"Decision letter placeholder for {journal}: {title}"
