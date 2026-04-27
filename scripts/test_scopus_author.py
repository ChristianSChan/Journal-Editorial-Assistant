"""Diagnostic command for Scopus author search/profile fields."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any

import requests

SCOPUS_AUTHOR_SEARCH_URL = "https://api.elsevier.com/content/search/author"
SCOPUS_AUTHOR_PROFILE_URL = "https://api.elsevier.com/content/author/author_id"
REQUEST_TIMEOUT_SECONDS = 20
MAX_PROFILE_LOOKUPS = 3


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print Scopus author endpoint diagnostics for one name and affiliation."
    )
    parser.add_argument("author_name")
    parser.add_argument("affiliation", nargs="?", default="")
    args = parser.parse_args()

    api_key = os.getenv("SCOPUS_API_KEY", "").strip()
    if not api_key:
        print("SCOPUS_API_KEY is not set.")
        print("Run: export SCOPUS_API_KEY=your_key_here")
        return 2

    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}
    query = _author_query(args.author_name, args.affiliation)

    print("Scopus author diagnostic")
    print("========================")
    print(f"Author: {args.author_name}")
    print(f"Affiliation: {args.affiliation or '(not supplied)'}")
    print(f"Author search query: {query}")
    print()

    search_payload = _call_endpoint(
        label="Author Search",
        url=SCOPUS_AUTHOR_SEARCH_URL,
        params={
            "query": query,
            "count": MAX_PROFILE_LOOKUPS,
            "view": "STANDARD",
        },
        headers=headers,
    )

    author_ids = _extract_author_ids(search_payload)
    if not author_ids:
        print("No Scopus author IDs found from Author Search.")
        return 1

    print(f"Author IDs selected for profile diagnostics: {', '.join(author_ids)}")
    print()

    for author_id in author_ids:
        _call_endpoint(
            label=f"Author Profile {author_id}",
            url=f"{SCOPUS_AUTHOR_PROFILE_URL}/{author_id}",
            params={"view": "ENHANCED"},
            headers=headers,
        )

    return 0


def _call_endpoint(
    label: str,
    url: str,
    params: dict[str, str | int],
    headers: dict[str, str],
) -> dict[str, Any] | None:
    print(f"[{label}]")
    print(f"URL: {url}")
    print(f"Params: {_safe_json(params)}")
    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        print(f"Status code: {response.status_code}")
    except requests.RequestException as exc:
        print("Succeeded: no")
        print(f"Failure: request error: {exc}")
        print()
        return None

    try:
        payload = response.json()
    except ValueError:
        print("Succeeded: no")
        print("Failure: response was not JSON")
        print(f"Response preview: {response.text[:500]}")
        print()
        return None

    if response.ok:
        print("Succeeded: yes")
    else:
        print("Succeeded: no")
        print(f"Failure body preview: {json.dumps(payload)[:800]}")

    field_paths = sorted(_field_paths(payload))
    print(f"Available fields returned ({len(field_paths)}):")
    for path in field_paths[:120]:
        print(f"  - {path}")
    if len(field_paths) > 120:
        print(f"  ... {len(field_paths) - 120} more fields")

    metric_paths = _metric_paths(payload)
    print("Metric field check:")
    print(f"  h-index present: {'yes' if metric_paths['h_index'] else 'no'}")
    for path, value in metric_paths["h_index"]:
        print(f"    - {path}: {value}")
    print(f"  citation fields present: {'yes' if metric_paths['citations'] else 'no'}")
    for path, value in metric_paths["citations"]:
        print(f"    - {path}: {value}")
    print()
    return payload


def _author_query(author_name: str, affiliation: str) -> str:
    first, last = _split_name(author_name)
    parts: list[str] = []
    if last:
        parts.append(f'AUTHLASTNAME("{last}")')
    if first:
        parts.append(f'AUTHFIRST("{first}")')
    if not parts:
        parts.append(f'AUTH("{author_name.strip()}")')
    if affiliation.strip():
        parts.append(f'AFFIL("{affiliation.strip()}")')
    return " AND ".join(parts)


def _split_name(author_name: str) -> tuple[str, str]:
    clean_name = re.sub(r"\s+", " ", author_name).strip()
    if not clean_name:
        return "", ""
    parts = clean_name.split(" ")
    if len(parts) == 1:
        return "", parts[0]
    return parts[0], parts[-1]


def _extract_author_ids(payload: dict[str, Any] | None) -> list[str]:
    if not payload:
        return []
    entries = ((payload.get("search-results") or {}).get("entry")) or []
    author_ids: list[str] = []
    for entry in entries if isinstance(entries, list) else []:
        author_id = (
            entry.get("dc:identifier", "").replace("AUTHOR_ID:", "")
            or entry.get("eid", "").replace("9-s2.0-", "")
            or entry.get("authorid")
        )
        if author_id and author_id not in author_ids:
            author_ids.append(str(author_id))
        if len(author_ids) >= MAX_PROFILE_LOOKUPS:
            break
    return author_ids


def _field_paths(value: Any, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.add(path)
            paths.update(_field_paths(child, path))
    elif isinstance(value, list):
        for child in value[:3]:
            path = f"{prefix}[]"
            paths.add(path)
            paths.update(_field_paths(child, path))
    return paths


def _metric_paths(payload: dict[str, Any]) -> dict[str, list[tuple[str, Any]]]:
    metrics = {"h_index": [], "citations": []}
    for path, value in _leaf_values(payload):
        key = path.rsplit(".", 1)[-1].casefold()
        compact_key = re.sub(r"[^a-z0-9]", "", key)
        if compact_key in {"hindex", "hidx"}:
            metrics["h_index"].append((path, value))
        if "citedby" in compact_key or "citation" in compact_key or compact_key in {
            "citedbycount",
            "citationcount",
        }:
            metrics["citations"].append((path, value))
    return metrics


def _leaf_values(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    leaves: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            leaves.extend(_leaf_values(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value[:3]):
            leaves.extend(_leaf_values(child, f"{prefix}[{index}]"))
    else:
        leaves.append((prefix, value))
    return leaves


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True)


if __name__ == "__main__":
    sys.exit(main())
