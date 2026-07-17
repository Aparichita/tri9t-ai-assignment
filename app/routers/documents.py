from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Document, DocumentNode, DocumentVersion
from app.schemas import (
    CompareResponse,
    DocumentOut,
    IngestRequest,
    IngestResponse,
    NodeDetail,
    NodeSummary,
    SearchHit,
    SectionChange,
    VersionOut,
)
from app.services import document_service as svc

router = APIRouter(tags=["documents"])


def _version_out(db: Session, version: DocumentVersion) -> VersionOut:
    count = db.scalar(
        select(func.count()).select_from(DocumentNode).where(DocumentNode.version_id == version.id)
    )
    return VersionOut(
        id=version.id,
        document_id=version.document_id,
        version_label=version.version_label,
        source_filename=version.source_filename,
        page_count=version.page_count,
        created_at=version.created_at,
        node_count=count or 0,
    )


@router.post("/documents/ingest", response_model=IngestResponse)
def ingest_document(payload: IngestRequest, db: Session = Depends(get_db)):
    try:
        document, version, nodes_created = svc.ingest_pdf(
            db,
            file_path=payload.file_path,
            document_key=payload.document_key,
            version_label=payload.version_label,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Ingest failed: {exc}") from exc

    return IngestResponse(
        document=DocumentOut.model_validate(document),
        version=_version_out(db, version),
        nodes_created=nodes_created,
        message=f"Ingested {version.source_filename} as {document.document_key}/{version.version_label}",
    )


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(db: Session = Depends(get_db)):
    docs = db.scalars(select(Document).order_by(Document.id)).all()
    return [DocumentOut.model_validate(d) for d in docs]


@router.get("/documents/{document_id}/versions", response_model=list[VersionOut])
def list_versions(document_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    versions = db.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.created_at)
    ).all()
    return [_version_out(db, v) for v in versions]


@router.get("/versions/{version_id}/sections", response_model=list[NodeSummary])
def list_sections(version_id: int, db: Session = Depends(get_db)):
    try:
        nodes = svc.list_sections(db, version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [
        NodeSummary(
            id=n.id,
            section_number=n.section_number,
            heading=n.heading,
            level=n.level,
            parent_id=n.parent_id,
            content_hash=n.content_hash,
            sort_order=n.sort_order,
            body_preview=(n.body[:160] + "…") if len(n.body) > 160 else n.body,
        )
        for n in nodes
    ]


@router.get("/nodes/{node_id}", response_model=NodeDetail)
def node_details(node_id: int, db: Session = Depends(get_db)):
    try:
        node = svc.get_node(db, node_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    children = db.scalars(
        select(DocumentNode)
        .where(DocumentNode.parent_id == node.id)
        .order_by(DocumentNode.sort_order)
    ).all()
    return NodeDetail(
        id=node.id,
        version_id=node.version_id,
        section_number=node.section_number,
        heading=node.heading,
        level=node.level,
        parent_id=node.parent_id,
        body=node.body,
        content_hash=node.content_hash,
        sort_order=node.sort_order,
        page_start=node.page_start,
        children=[
            NodeSummary(
                id=c.id,
                section_number=c.section_number,
                heading=c.heading,
                level=c.level,
                parent_id=c.parent_id,
                content_hash=c.content_hash,
                sort_order=c.sort_order,
                body_preview=(c.body[:120] + "…") if len(c.body) > 120 else c.body,
            )
            for c in children
        ],
    )


@router.get("/versions/{version_id}/search", response_model=list[SearchHit])
def search(version_id: int, q: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    try:
        hits = svc.search_sections(db, version_id, q)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [
        SearchHit(
            id=node.id,
            section_number=node.section_number,
            heading=node.heading,
            level=node.level,
            content_hash=node.content_hash,
            snippet=snippet,
        )
        for node, snippet in hits
    ]


@router.get("/versions/compare", response_model=CompareResponse)
def compare(
    version_a_id: int = Query(...),
    version_b_id: int = Query(...),
    db: Session = Depends(get_db),
):
    try:
        result = svc.compare_versions(db, version_a_id, version_b_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CompareResponse(
        version_a_id=result["version_a_id"],
        version_b_id=result["version_b_id"],
        added=[SectionChange(**c) for c in result["added"]],
        removed=[SectionChange(**c) for c in result["removed"]],
        modified=[SectionChange(**c) for c in result["modified"]],
        unchanged_count=result["unchanged_count"],
    )
