"""Business logic for ingest, compare, selections, generation, staleness."""

from __future__ import annotations

import json
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import BASE_DIR, settings
from app.models import (
    Document,
    DocumentNode,
    DocumentVersion,
    Generation,
    Selection,
    SelectionItem,
)
from app.parser.pdf_parser import parse_pdf
from app.services.gemini_client import generate_qa_test_cases


def infer_document_key_and_version(filename: str) -> tuple[str, str]:
    """ct200_manual.pdf → (ct200_manual, v1); ct200_manual_v2.pdf → (ct200_manual, v2)."""
    stem = Path(filename).stem
    match = re.match(r"^(?P<key>.+)_v(?P<ver>\d+)$", stem, re.IGNORECASE)
    if match:
        return match.group("key"), f"v{match.group('ver')}"
    return stem, "v1"


def resolve_pdf_path(file_path: str) -> Path:
    path = Path(file_path)
    if not path.is_absolute():
        path = BASE_DIR / path
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError("Only PDF files are supported")
    return path.resolve()


def ingest_pdf(
    db: Session,
    file_path: str,
    document_key: str | None = None,
    version_label: str | None = None,
) -> tuple[Document, DocumentVersion, int]:
    path = resolve_pdf_path(file_path)
    key, default_version = infer_document_key_and_version(path.name)
    document_key = document_key or key
    version_label = version_label or default_version

    title, parsed_nodes, page_count = parse_pdf(str(path))

    document = db.scalar(select(Document).where(Document.document_key == document_key))
    if document is None:
        document = Document(document_key=document_key, title=title)
        db.add(document)
        db.flush()
    elif title and not document.title:
        document.title = title

    existing = db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.version_label == version_label,
        )
    )
    if existing is not None:
        raise ValueError(
            f"Version '{version_label}' already exists for '{document_key}'. "
            "Versions are immutable — ingest with a new version_label."
        )

    version = DocumentVersion(
        document_id=document.id,
        version_label=version_label,
        source_filename=path.name,
        source_path=str(path),
        page_count=page_count,
    )
    db.add(version)
    db.flush()

    # First pass: create nodes; second pass: wire parent_id from section numbers
    section_to_node: dict[str, DocumentNode] = {}
    for parsed in parsed_nodes:
        node = DocumentNode(
            version_id=version.id,
            section_number=parsed.section_number,
            heading=parsed.heading,
            level=parsed.level,
            body=parsed.body,
            content_hash=parsed.content_hash,
            sort_order=parsed.sort_order,
            page_start=parsed.page_start,
        )
        db.add(node)
        db.flush()
        section_to_node[parsed.section_number] = node

    for parsed in parsed_nodes:
        if parsed.parent_section and parsed.parent_section in section_to_node:
            section_to_node[parsed.section_number].parent_id = section_to_node[
                parsed.parent_section
            ].id

    db.commit()
    db.refresh(document)
    db.refresh(version)
    return document, version, len(parsed_nodes)


def list_sections(db: Session, version_id: int) -> list[DocumentNode]:
    version = db.get(DocumentVersion, version_id)
    if version is None:
        raise LookupError(f"Version {version_id} not found")
    return list(
        db.scalars(
            select(DocumentNode)
            .where(DocumentNode.version_id == version_id)
            .order_by(DocumentNode.sort_order)
        ).all()
    )


def get_node(db: Session, node_id: int) -> DocumentNode:
    node = db.get(DocumentNode, node_id)
    if node is None:
        raise LookupError(f"Node {node_id} not found")
    return node


def search_sections(db: Session, version_id: int, query: str) -> list[tuple[DocumentNode, str]]:
    if not query.strip():
        return []
    q = query.strip().lower()
    nodes = list_sections(db, version_id)
    hits: list[tuple[DocumentNode, str]] = []
    for node in nodes:
        haystack = f"{node.section_number} {node.heading} {node.body}".lower()
        if q not in haystack:
            continue
        idx = haystack.find(q)
        start = max(0, idx - 40)
        end = min(len(haystack), idx + len(q) + 40)
        snippet = haystack[start:end].strip()
        hits.append((node, snippet))
    return hits


def compare_versions(db: Session, version_a_id: int, version_b_id: int) -> dict:
    a = db.get(DocumentVersion, version_a_id)
    b = db.get(DocumentVersion, version_b_id)
    if a is None or b is None:
        raise LookupError("One or both versions not found")
    if a.document_id != b.document_id:
        raise ValueError("Versions belong to different documents")

    nodes_a = {n.section_number: n for n in list_sections(db, version_a_id)}
    nodes_b = {n.section_number: n for n in list_sections(db, version_b_id)}
    all_sections = sorted(set(nodes_a) | set(nodes_b), key=_section_sort_key)

    added, removed, modified = [], [], []
    unchanged = 0
    for sec in all_sections:
        na, nb = nodes_a.get(sec), nodes_b.get(sec)
        if na and not nb:
            removed.append(
                {
                    "section_number": sec,
                    "change_type": "removed",
                    "heading_v1": na.heading,
                    "heading_v2": None,
                    "hash_v1": na.content_hash,
                    "hash_v2": None,
                }
            )
        elif nb and not na:
            added.append(
                {
                    "section_number": sec,
                    "change_type": "added",
                    "heading_v1": None,
                    "heading_v2": nb.heading,
                    "hash_v1": None,
                    "hash_v2": nb.content_hash,
                }
            )
        elif na and nb:
            if na.content_hash != nb.content_hash:
                modified.append(
                    {
                        "section_number": sec,
                        "change_type": "modified",
                        "heading_v1": na.heading,
                        "heading_v2": nb.heading,
                        "hash_v1": na.content_hash,
                        "hash_v2": nb.content_hash,
                    }
                )
            else:
                unchanged += 1

    return {
        "version_a_id": version_a_id,
        "version_b_id": version_b_id,
        "added": added,
        "removed": removed,
        "modified": modified,
        "unchanged_count": unchanged,
    }


def _section_sort_key(section: str) -> tuple:
    try:
        return tuple(int(p) for p in section.split("."))
    except ValueError:
        return (section,)


def create_selection(
    db: Session, version_id: int, node_ids: list[int], name: str = ""
) -> Selection:
    version = db.get(DocumentVersion, version_id)
    if version is None:
        raise LookupError(f"Version {version_id} not found")

    nodes = list(
        db.scalars(
            select(DocumentNode).where(
                DocumentNode.id.in_(node_ids),
                DocumentNode.version_id == version_id,
            )
        ).all()
    )
    found_ids = {n.id for n in nodes}
    missing = [nid for nid in node_ids if nid not in found_ids]
    if missing:
        raise ValueError(
            f"Nodes {missing} are not part of version {version_id}. "
            "Selections are version-pinned."
        )

    selection = Selection(version_id=version_id, name=name or f"selection-{version_id}")
    db.add(selection)
    db.flush()
    for node in nodes:
        db.add(SelectionItem(selection_id=selection.id, node_id=node.id))
    db.commit()
    db.refresh(selection)
    return selection


def get_selection(db: Session, selection_id: int) -> Selection:
    selection = db.scalar(
        select(Selection)
        .options(joinedload(Selection.items).joinedload(SelectionItem.node))
        .where(Selection.id == selection_id)
    )
    if selection is None:
        raise LookupError(f"Selection {selection_id} not found")
    return selection


def _generations_dir() -> Path:
    path = Path(settings.generations_dir)
    if not path.is_absolute():
        path = BASE_DIR / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_generation(db: Session, selection_id: int) -> Generation:
    selection = get_selection(db, selection_id)
    nodes = [item.node for item in selection.items]
    if not nodes:
        raise ValueError("Selection has no nodes")

    sections_payload = [
        {
            "section_number": n.section_number,
            "heading": n.heading,
            "body": n.body,
            "content_hash": n.content_hash,
        }
        for n in nodes
    ]
    source_hashes = {n.section_number: n.content_hash for n in nodes}

    generation = Generation(
        selection_id=selection.id,
        version_id=selection.version_id,
        status="pending",
        model_name=settings.gemini_model,
        json_path="",
        source_hashes_json=json.dumps(source_hashes),
    )
    db.add(generation)
    db.flush()

    try:
        test_cases = generate_qa_test_cases(sections_payload)
        payload = {
            "generation_id": generation.id,
            "selection_id": selection.id,
            "version_id": selection.version_id,
            "model_name": settings.gemini_model,
            "source_sections": sections_payload,
            "source_hashes": source_hashes,
            "test_cases": test_cases,
        }
        json_path = _generations_dir() / f"generation_{generation.id}.json"
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        generation.json_path = str(json_path)
        generation.status = "completed"
        generation.error_message = None
    except Exception as exc:  # noqa: BLE001 — surface AI failures cleanly to API
        generation.status = "failed"
        generation.error_message = str(exc)
        payload = {
            "generation_id": generation.id,
            "selection_id": selection.id,
            "version_id": selection.version_id,
            "model_name": settings.gemini_model,
            "source_sections": sections_payload,
            "source_hashes": source_hashes,
            "error": str(exc),
            "test_cases": [],
        }
        json_path = _generations_dir() / f"generation_{generation.id}.json"
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        generation.json_path = str(json_path)

    db.commit()
    db.refresh(generation)
    return generation


def load_generation_json(generation: Generation) -> dict:
    if not generation.json_path:
        return {}
    path = Path(generation.json_path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def check_staleness(db: Session, generation_id: int, against_version_id: int | None = None) -> dict:
    """A generation is stale if any source section hash changed in a later (or target) version."""
    generation = db.get(Generation, generation_id)
    if generation is None:
        raise LookupError(f"Generation {generation_id} not found")

    source_version = db.get(DocumentVersion, generation.version_id)
    if source_version is None:
        raise LookupError("Source version missing")

    if against_version_id is None:
        later = list(
            db.scalars(
                select(DocumentVersion)
                .where(
                    DocumentVersion.document_id == source_version.document_id,
                    DocumentVersion.id != source_version.id,
                    DocumentVersion.created_at >= source_version.created_at,
                )
                .order_by(DocumentVersion.created_at.desc())
            ).all()
        )
        compare_version = later[0] if later else source_version
    else:
        compare_version = db.get(DocumentVersion, against_version_id)
        if compare_version is None:
            raise LookupError(f"Version {against_version_id} not found")
        if compare_version.document_id != source_version.document_id:
            raise ValueError("Compare version belongs to a different document")

    source_hashes = json.loads(generation.source_hashes_json or "{}")
    compare_nodes = {
        n.section_number: n for n in list_sections(db, compare_version.id)
    }

    changed = []
    for section, old_hash in source_hashes.items():
        current = compare_nodes.get(section)
        if current is None:
            changed.append(
                {
                    "section_number": section,
                    "change_type": "removed",
                    "heading_v1": None,
                    "heading_v2": None,
                    "hash_v1": old_hash,
                    "hash_v2": None,
                }
            )
        elif current.content_hash != old_hash:
            changed.append(
                {
                    "section_number": section,
                    "change_type": "modified",
                    "heading_v1": None,
                    "heading_v2": current.heading,
                    "hash_v1": old_hash,
                    "hash_v2": current.content_hash,
                }
            )

    is_stale = bool(changed) and compare_version.id != source_version.id
    # Also stale if comparing same version? No — same version same hashes → current.
    if compare_version.id == source_version.id:
        is_stale = False
        reason = "No later version to compare; generation is current for its source version."
    elif is_stale:
        secs = ", ".join(c["section_number"] for c in changed)
        reason = (
            f"Source section content changed in version '{compare_version.version_label}' "
            f"(sections: {secs}). Re-run generation against the new selection."
        )
    else:
        reason = (
            f"All selected sections unchanged in version '{compare_version.version_label}'."
        )

    generation.is_stale = is_stale
    generation.stale_reason = reason
    db.commit()

    return {
        "generation_id": generation.id,
        "is_stale": is_stale,
        "reason": reason,
        "compared_against_version_id": compare_version.id,
        "changed_sections": changed,
    }


def get_generation(db: Session, generation_id: int) -> tuple[Generation, dict]:
    generation = db.get(Generation, generation_id)
    if generation is None:
        raise LookupError(f"Generation {generation_id} not found")
    # Refresh staleness against latest later version if any
    try:
        check_staleness(db, generation_id)
        db.refresh(generation)
    except Exception:  # noqa: BLE001
        pass
    payload = load_generation_json(generation)
    return generation, payload
