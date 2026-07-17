"""PDF → hierarchical document tree.

Strategy (interview-friendly):
1. Extract plain text page-by-page with PyMuPDF.
2. Normalize Unicode ligatures / punctuation.
3. Detect numbered headings with a regex (not font heuristics alone).
4. Rebuild parent links from the section number (handles skipped levels).
5. Flatten table-like short lines into readable body text.
6. Hash normalized body so version diffs are content-based.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

import fitz

# "1.", "1.1", "2.1.1.1", optional trailing period before title
HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$")

LIGATURE_MAP = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\u2013": "-",
    "\u2014": "-",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u00b1": "+/-",
    "\u2265": ">=",
    "\u2264": "<=",
}


@dataclass
class ParsedNode:
    section_number: str
    heading: str
    level: int
    body: str = ""
    parent_section: str | None = None
    content_hash: str = ""
    sort_order: int = 0
    page_start: int | None = None
    children: list[ParsedNode] = field(default_factory=list)


def normalize_text(text: str) -> str:
    """Fix ligatures and collapse whitespace for hashing / storage."""
    for src, dst in LIGATURE_MAP.items():
        text = text.replace(src, dst)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def content_hash(body: str, heading: str = "") -> str:
    """SHA-256 of normalized heading+body. Empty body still hashes heading."""
    payload = normalize_text(f"{heading}\n{body}").lower()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def section_level(section_number: str) -> int:
    return len(section_number.split("."))


def parent_section_number(section_number: str) -> str | None:
    """Parent of 2.1.1.1 is 2.1.1 even if that node was skipped in the PDF."""
    parts = section_number.split(".")
    if len(parts) <= 1:
        return None
    return ".".join(parts[:-1])


def find_existing_ancestor(section_number: str, known: dict[str, ParsedNode]) -> str | None:
    """Walk up section prefixes until we find a node that exists.

    Handles skipped hierarchy: 2.1.1.1 under 2.1 when 2.1.1 is missing.
    """
    parent = parent_section_number(section_number)
    while parent is not None:
        if parent in known:
            return parent
        parent = parent_section_number(parent)
    return None


def flatten_table_lines(lines: list[str]) -> str:
    """Join short consecutive lines (typical table cells) into readable text."""
    if not lines:
        return ""

    chunks: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        avg_len = sum(len(x) for x in buffer) / len(buffer)
        if len(buffer) >= 3 and avg_len < 40:
            chunks.append(" | ".join(buffer))
        else:
            chunks.append(" ".join(buffer))
        buffer = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        buffer.append(stripped)
    flush()
    return "\n".join(chunks).strip()


def extract_lines(pdf_path: str) -> list[tuple[int, str]]:
    """Return (page_number_1based, line_text) pairs."""
    doc = fitz.open(pdf_path)
    lines: list[tuple[int, str]] = []
    try:
        for page_index in range(len(doc)):
            page = doc[page_index]
            text = page.get_text("text")
            for raw in text.splitlines():
                cleaned = normalize_text(raw)
                if cleaned:
                    lines.append((page_index + 1, cleaned))
    finally:
        doc.close()
    return lines


def parse_pdf(pdf_path: str) -> tuple[str, list[ParsedNode], int]:
    """Parse a medical-device PDF into ordered ParsedNode list + title + page count."""
    doc = fitz.open(pdf_path)
    page_count = len(doc)
    first_page = normalize_text(doc[0].get_text("text")) if page_count else ""
    doc.close()

    lines = extract_lines(pdf_path)
    title_parts: list[str] = []
    nodes: list[ParsedNode] = []
    known: dict[str, ParsedNode] = {}
    current: ParsedNode | None = None
    body_lines: list[str] = []
    sort_order = 0

    def commit_body() -> None:
        nonlocal body_lines, current
        if current is None:
            return
        current.body = flatten_table_lines(body_lines)
        current.content_hash = content_hash(current.body, current.heading)
        body_lines = []

    for page_no, line in lines:
        match = HEADING_RE.match(line)
        if match:
            section_number, heading = match.group(1), match.group(2).strip()
            if _looks_like_body_list_item(heading) and known:
                if current is not None:
                    body_lines.append(line)
                continue

            commit_body()
            sort_order += 1
            level = section_level(section_number)
            parent = find_existing_ancestor(section_number, known)

            # Duplicate headings (e. g. "Error Codes" under 4.2 and 7.1) are fine:
            # section_number is the unique key; heading text may repeat.
            node = ParsedNode(
                section_number=section_number,
                heading=heading,
                level=level,
                parent_section=parent,
                sort_order=sort_order,
                page_start=page_no,
            )
            # Out-of-order numbering (3.4 before 3.3): keep PDF order via sort_order.
            nodes.append(node)
            known[section_number] = node
            current = node
            continue

        if current is None:
            if not HEADING_RE.match(line):
                title_parts.append(line)
            continue

        body_lines.append(line)

    commit_body()

    title = " ".join(title_parts).strip() or first_page.split("\n")[0][:200]
    return title, nodes, page_count


def _looks_like_body_list_item(heading: str) -> bool:
    """Heuristic: classification/enum lines like '1. Normal: systolic ...'."""
    if ":" in heading and len(heading) > 40:
        return True
    lower = heading.lower()
    if lower.startswith(("normal:", "elevated:", "hypertension")):
        return True
    return False
