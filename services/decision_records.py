"""Local storage for editorial decision records."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


RECORDS_DIR = Path(__file__).resolve().parents[1] / "decision_records"


def list_decision_records() -> list[dict[str, Any]]:
    """Return saved decision records, newest first."""
    RECORDS_DIR.mkdir(exist_ok=True)
    records: list[dict[str, Any]] = []
    for path in RECORDS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        data["_json_path"] = str(path)
        data["_markdown_path"] = str(path.with_suffix(".md"))
        records.append(data)
    return sorted(records, key=lambda item: str(item.get("saved_at", "")), reverse=True)


def save_decision_record(
    *,
    journal_name: str,
    manuscript_title: str,
    selected_decision: str,
    editor_points: str,
    selected_highlights: dict[str, list[str]],
    analysis: dict[str, Any],
    reviewers: list[dict[str, Any]],
    markdown: str,
) -> dict[str, str]:
    """Save one decision record as JSON plus Markdown."""
    RECORDS_DIR.mkdir(exist_ok=True)
    saved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    record_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{_slug(manuscript_title or 'decision-record')}"
    json_path = RECORDS_DIR / f"{record_id}.json"
    markdown_path = RECORDS_DIR / f"{record_id}.md"
    payload = {
        "record_id": record_id,
        "saved_at": saved_at,
        "journal_name": journal_name,
        "manuscript_title": manuscript_title,
        "selected_decision": selected_decision,
        "editor_points": editor_points,
        "selected_highlights": selected_highlights,
        "analysis": analysis,
        "reviewers": reviewers,
        "markdown_path": str(markdown_path),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return {"record_id": record_id, "json_path": str(json_path), "markdown_path": str(markdown_path)}


def load_record_markdown(record: dict[str, Any]) -> str:
    markdown_path = Path(str(record.get("markdown_path") or record.get("_markdown_path") or ""))
    if markdown_path.exists():
        return markdown_path.read_text(encoding="utf-8")
    return _record_to_text(record)


def previous_round_context(record: dict[str, Any]) -> str:
    """Build compact context focused on prior concerns and required revisions."""
    analysis = record.get("analysis") if isinstance(record.get("analysis"), dict) else {}
    reviewers = record.get("reviewers") if isinstance(record.get("reviewers"), list) else []
    selected_highlights = record.get("selected_highlights")
    parts = [
        f"Prior saved decision record: {record.get('record_id', 'unknown')}",
        f"Saved: {record.get('saved_at', 'unknown')}",
        f"Journal: {record.get('journal_name', 'not specified')}",
        f"Manuscript title: {record.get('manuscript_title', 'not specified')}",
        f"Prior editor-selected decision: {record.get('selected_decision', 'not specified')}",
        "",
        "Prior reviewer synthesis:",
        str(analysis.get("reviewer_consensus_summary", "")),
        "",
        "Prior main concerns:",
        _bullet_text(analysis.get("main_concerns", [])),
        "",
        "Prior reviewer convergence:",
        _bullet_text(analysis.get("reviewer_convergence", [])),
        "",
        "Prior reviewer divergence:",
        _bullet_text(analysis.get("reviewer_divergence", [])),
        "",
        "Prior statistics flags:",
        _bullet_text(analysis.get("statistics_flags", [])),
        "",
        "Prior open science flags:",
        _bullet_text(analysis.get("open_science_flags", [])),
        "",
        "Prior editor attention points:",
        _bullet_text(analysis.get("editor_attention_points", [])),
        "",
        "Prior selected highlights:",
        _selected_highlights_text(selected_highlights),
        "",
        "Raw prior reviewer comments:",
    ]
    for reviewer in reviewers:
        if not isinstance(reviewer, dict):
            continue
        parts.extend(
            [
                f"{reviewer.get('reviewer_label', 'Reviewer')}: {reviewer.get('recommendation', 'No recommendation')}",
                str(reviewer.get("comments", "")),
                "",
            ]
        )
    return "\n".join(parts).strip()


def _record_to_text(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, indent=2)


def _bullet_text(items: object) -> str:
    if not isinstance(items, list) or not items:
        return "- None recorded."
    return "\n".join(f"- {str(item)}" for item in items)


def _selected_highlights_text(selected_highlights: object) -> str:
    if not isinstance(selected_highlights, dict) or not selected_highlights:
        return "- None selected."
    lines: list[str] = []
    for section, items in selected_highlights.items():
        lines.append(str(section))
        if isinstance(items, list):
            lines.extend(f"- {item}" for item in items)
    return "\n".join(lines)


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().lower()).strip("-")
    return slug[:70] or "decision-record"
