"""PDF manuscript metadata extraction helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from io import BytesIO

import fitz
from pydantic import BaseModel, Field
from pypdf import PdfReader

from services.llm_assist import extract_manuscript_fields_with_llm

MAX_PAGES = 8
MAX_TEXT_CHARS = 80_000
MAX_PREVIEW_CHARS_PER_PAGE = 2_500


class ExtractedManuscriptFields(BaseModel):
    journal_name: str = ""
    title: str = ""
    abstract: str = ""
    keywords: list[str] = Field(default_factory=list)
    text_preview: str = ""
    page_previews: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


@dataclass
class PdfTextExtraction:
    text: str
    page_texts: list[str]
    metadata_title: str = ""
    title_candidates: list[str] | None = None
    engine: str = ""


def extract_manuscript_fields_from_pdf(pdf_bytes: bytes) -> ExtractedManuscriptFields:
    """Extract editable manuscript fields from a PDF using layout-aware heuristics."""
    extraction = _extract_with_pymupdf(pdf_bytes)
    if not extraction.text.strip():
        extraction = _extract_with_pypdf(pdf_bytes)

    fields = ExtractedManuscriptFields(
        text_preview=extraction.text[: MAX_PREVIEW_CHARS_PER_PAGE * 2],
        page_previews=[page[:MAX_PREVIEW_CHARS_PER_PAGE] for page in extraction.page_texts],
    )

    if extraction.engine:
        fields.notes.append(f"PDF text extracted with {extraction.engine}.")
    if not extraction.text.strip():
        fields.notes.append(
            "No selectable text found. This may be a scanned PDF; OCR is not enabled yet."
        )
        return fields

    lines = _meaningful_lines(extraction.text)
    fields.journal_name = _extract_journal_name(extraction.text, lines)
    fields.title = _extract_title(
        extraction.text,
        lines,
        extraction.metadata_title,
        extraction.title_candidates or [],
    )
    fields.abstract = _extract_abstract(extraction.text)
    fields.keywords = _extract_keywords(extraction.text)
    llm_fields = extract_manuscript_fields_with_llm(extraction.text)
    if llm_fields.used_llm:
        fields.notes.append("LLM-assisted metadata extraction was applied conservatively.")
        if llm_fields.journal_name:
            fields.journal_name = llm_fields.journal_name
        if llm_fields.title and len(llm_fields.title) >= len(fields.title):
            fields.title = llm_fields.title
        if llm_fields.abstract and (not fields.abstract or len(llm_fields.abstract) > len(fields.abstract)):
            fields.abstract = llm_fields.abstract
        if llm_fields.keywords:
            fields.keywords = llm_fields.keywords
        fields.notes.extend(llm_fields.notes)
    elif llm_fields.notes:
        fields.notes.extend(llm_fields.notes)

    _sanitize_extracted_fields(fields)

    if not fields.journal_name:
        fields.notes.append("Journal name was not clearly identified.")
    if not fields.title:
        fields.notes.append("Title was not clearly identified.")
    if not fields.abstract:
        fields.notes.append("Abstract was not clearly identified.")
    if not fields.keywords:
        fields.notes.append("Keywords were not clearly identified.")

    return fields


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Return selectable PDF text for decision-assistant context."""
    extraction = _extract_with_pymupdf(pdf_bytes)
    if not extraction.text.strip():
        extraction = _extract_with_pypdf(pdf_bytes)
    return extraction.text[:MAX_TEXT_CHARS]


def _extract_with_pymupdf(pdf_bytes: bytes) -> PdfTextExtraction:
    page_texts: list[str] = []
    title_candidates: list[str] = []
    metadata_title = ""

    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return PdfTextExtraction(text="", page_texts=[], engine="PyMuPDF failed")

    metadata_title = _clean_field(document.metadata.get("title", ""), max_length=250)
    if metadata_title.lower() in {"untitled", "manuscript", "article"}:
        metadata_title = ""

    for page_index, page in enumerate(document[:MAX_PAGES]):
        page_texts.append(page.get_text("text") or "")
        if page_index == 0:
            title_candidates.extend(_pymupdf_title_candidates(page))

    text = _normalize_text("\n".join(page_texts))[:MAX_TEXT_CHARS]
    return PdfTextExtraction(
        text=text,
        page_texts=[_normalize_text(page_text) for page_text in page_texts],
        metadata_title=metadata_title,
        title_candidates=title_candidates,
        engine="PyMuPDF",
    )


def _extract_with_pypdf(pdf_bytes: bytes) -> PdfTextExtraction:
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
    except Exception:
        return PdfTextExtraction(text="", page_texts=[], engine="pypdf failed")

    page_texts: list[str] = []
    for page in reader.pages[:MAX_PAGES]:
        page_texts.append(page.extract_text() or "")

    metadata_title = ""
    try:
        metadata_title = _clean_field(reader.metadata.title if reader.metadata else "")
    except Exception:
        metadata_title = ""
    if metadata_title.lower() in {"untitled", "manuscript", "article"}:
        metadata_title = ""

    text = _normalize_text("\n".join(page_texts))[:MAX_TEXT_CHARS]
    return PdfTextExtraction(
        text=text,
        page_texts=[_normalize_text(page_text) for page_text in page_texts],
        metadata_title=metadata_title,
        title_candidates=[],
        engine="pypdf fallback",
    )


def _pymupdf_title_candidates(page: fitz.Page) -> list[str]:
    blocks = page.get_text("dict").get("blocks", [])
    line_infos: list[tuple[float, float, str]] = []
    for block in blocks:
        for line in block.get("lines", []):
            line_text_parts: list[str] = []
            max_size = 0.0
            top = float(line.get("bbox", [0, 0, 0, 0])[1])
            for span in line.get("spans", []):
                text = _clean_field(span.get("text", ""), max_length=500)
                if not text:
                    continue
                line_text_parts.append(text)
                max_size = max(max_size, float(span.get("size", 0)))
            line_text = _clean_field(" ".join(line_text_parts), max_length=500)
            if line_text and _is_title_candidate(line_text, allow_short=True):
                line_infos.append((max_size, top, line_text))

    if not line_infos:
        return []

    line_infos.sort(key=lambda item: (item[1], -item[0]))
    largest_size = max(size for size, _, _ in line_infos)
    title_lines = [
        text
        for size, top, text in line_infos
        if size >= largest_size - 1.0 and top < 350
    ]
    joined = _clean_field(" ".join(title_lines), max_length=500)
    candidates = [joined] if _is_title_candidate(joined) else []
    candidates.extend(text for _, _, text in line_infos if _is_title_candidate(text))

    deduped: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return deduped[:4]


def _meaningful_lines(text: str) -> list[str]:
    lines = [_clean_field(line, max_length=250) for line in text.splitlines()]
    return [line for line in lines if line]


def _extract_journal_name(text: str, lines: list[str]) -> str:
    for line in lines[:35]:
        if re.search(r"^(journal|submitted to|submission to|target journal|intended journal|journal submitted to)\b", line, re.IGNORECASE):
            cleaned = re.sub(
                r"^(journal|submitted to|submission to|target journal|intended journal|journal submitted to)[:\s]*",
                "",
                line,
                flags=re.IGNORECASE,
            )
            return _clean_field(cleaned)
        if re.search(r"^(Journal|Annals|Proceedings|Transactions|Review|Reviews|Letters)\b", line):
            return line

    compact = _single_line(text)
    patterns = [
        r"(?:submitted to|submission to|target journal|intended journal|journal submitted to)[:\s]+(.{4,140}?)(?:\s+(?:title|manuscript title|abstract|keywords?|article type|manuscript type)\b|$)",
        r"(?:journal)[:\s]+(.{4,140}?)(?:\s+(?:title|manuscript title|abstract|keywords?|article type|manuscript type)\b|$)",
        r"((?:Journal|Annals|Proceedings|Transactions|Review|Reviews|Letters) [A-Z][A-Za-z0-9 &,:'-]{4,90}?)(?:\s+(?:title|manuscript title|abstract|keywords?)\b|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, compact, flags=re.IGNORECASE)
        if match:
            return _clean_field(match.group(1))
    return ""


def _extract_title(
    text: str,
    lines: list[str],
    metadata_title: str,
    layout_candidates: list[str],
) -> str:
    explicit = re.search(
        r"(?:^|\n|\s)(?:title|manuscript title)\s*:\s*(.{10,500}?)(?:\s+(?:abstract|authors?|keywords?|introduction)\b)",
        _single_line(text),
        flags=re.IGNORECASE,
    )
    if explicit:
        return _clean_field(explicit.group(1), max_length=500)

    for candidate in layout_candidates:
        if _is_title_candidate(candidate):
            return candidate

    if metadata_title:
        return metadata_title

    before_abstract = _lines_before_label(lines, "abstract")
    title_lines = _contiguous_title_lines(before_abstract[:45])
    if title_lines:
        return _clean_field(" ".join(title_lines), max_length=500)

    candidates = [line for line in before_abstract[:45] if _is_title_candidate(line)]
    return candidates[0] if candidates else ""


def _contiguous_title_lines(lines: list[str]) -> list[str]:
    title_lines: list[str] = []
    started = False
    for line in lines:
        if re.search(r"^(journal|submitted|submission|doi|title|manuscript title)\b", line, re.IGNORECASE):
            continue
        if _looks_like_author_or_affiliation(line):
            if started:
                break
            continue
        if not _is_title_candidate(line, allow_short=started):
            if started:
                break
            continue
        title_lines.append(line)
        started = True
        if len(" ".join(title_lines)) > 420 or len(title_lines) >= 5:
            break
    return title_lines


def _extract_abstract(text: str) -> str:
    compact = _single_line(text)
    match = re.search(
        r"\babstract\b[:\s]*(.*?)(?:\bkeywords?\b|\bkey words\b|\b1\.\s*introduction\b|\bintroduction\b)",
        compact,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    return _clean_field(match.group(1), max_length=3000)


def _extract_keywords(text: str) -> list[str]:
    lines = _meaningful_lines(text)
    for index, line in enumerate(lines):
        if not re.match(r"^(keywords?|key words)\b", line, flags=re.IGNORECASE):
            continue

        raw = re.sub(r"^(keywords?|key words)[:\s]*", "", line, flags=re.IGNORECASE)
        continuation_lines: list[str] = []
        for next_line in lines[index + 1 : index + 4]:
            if _is_section_heading(next_line) or _is_title_candidate(next_line):
                break
            continuation_lines.append(next_line)

        raw_keywords = _clean_field(" ".join([raw, *continuation_lines]), max_length=700)
        parsed = _parse_keyword_list(raw_keywords)
        if parsed:
            return parsed

    compact = _single_line(text)
    match = re.search(
        r"\b(?:keywords?|key words)\b[:\s]*(.*?)(?:\bintroduction\b|\bbackground\b|\bmethods?\b|\bmaterials and methods\b|$)",
        compact,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return []
    return _parse_keyword_list(_clean_field(match.group(1), max_length=700))


def _parse_keyword_list(raw_keywords: str) -> list[str]:
    raw_keywords = re.sub(r"\b(introduction|background|methods?|materials and methods|results|discussion|conclusion)\b.*$", "", raw_keywords, flags=re.IGNORECASE)
    if len(raw_keywords) > 500:
        return []
    parts = re.split(r"[,;|]", raw_keywords)
    keywords: list[str] = []
    for part in parts:
        cleaned = _clean_field(part, max_length=120)
        if not 2 <= len(cleaned) <= 80:
            continue
        if len(cleaned.split()) > 8:
            continue
        if re.search(r"^(introduction|background|methods?|abstract)\b", cleaned, re.IGNORECASE):
            continue
        if cleaned.count(".") > 1:
            continue
        keywords.append(cleaned)
    return keywords[:12]


def _lines_before_label(lines: list[str], label: str) -> list[str]:
    for index, line in enumerate(lines):
        if re.fullmatch(label, line, flags=re.IGNORECASE) or re.match(
            rf"^{label}\b",
            line,
            flags=re.IGNORECASE,
        ):
            return lines[:index]
    return lines


def _is_title_candidate(line: str, allow_short: bool = False) -> bool:
    min_length = 4 if allow_short else 8
    if not min_length <= len(line) <= 500:
        return False
    if re.search(r"^(journal|submitted|submission|doi|author|affiliation|keywords?|abstract)\b", line, re.IGNORECASE):
        return False
    if re.search(r"@|https?://|www\.", line, re.IGNORECASE):
        return False
    if not allow_short and len(line.split()) < 3:
        return False
    return True


def _is_section_heading(line: str) -> bool:
    return re.match(
        r"^(abstract|keywords?|key words|introduction|background|methods?|materials and methods|results|discussion|conclusion)\b",
        line,
        flags=re.IGNORECASE,
    ) is not None


def _looks_like_author_or_affiliation(line: str) -> bool:
    if re.search(r"\b(university|college|hospital|department|institute|school|center|centre)\b", line, re.IGNORECASE):
        return True
    if re.search(r"\d", line) and len(line.split()) <= 8:
        return True
    if "," in line and len(line.split()) <= 14:
        return True
    if re.fullmatch(r"([A-Z][A-Za-z.-]+\s+){1,5}[A-Z][A-Za-z.-]+", line):
        return True
    return False


def _normalize_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _single_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _clean_field(value: str, max_length: int = 300) -> str:
    value = _plain_text(value)
    value = re.sub(r"\s+", " ", value or "").strip(" .:-\t")
    return value[:max_length].strip()


def _sanitize_extracted_fields(fields: ExtractedManuscriptFields) -> None:
    fields.journal_name = _clean_field(fields.journal_name, max_length=180)
    fields.title = _clean_title_field(fields.title)
    fields.abstract = _clean_field(fields.abstract, max_length=3500)
    fields.keywords = [
        keyword
        for keyword in (_clean_field(keyword, max_length=120) for keyword in fields.keywords)
        if keyword
    ][:12]


def _clean_title_field(value: str) -> str:
    value = _clean_field(value, max_length=500)
    value = re.sub(
        r"^(title|manuscript title|article title)\s*[:\-]\s*",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\s+", " ", value).strip(" .:-\t")
    return value


def _plain_text(value: str) -> str:
    value = unescape(str(value or ""))
    value = re.sub(r"</?(?:strong|b|em|i|u|span|p|div|br|sub|sup)[^>]*>", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]{1,80}>", " ", value)
    value = re.sub(r"\b(?:strong|em|span|div|html|body)\b\s*", " ", value, flags=re.IGNORECASE)
    return value
