from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DocumentVersion
from app.schemas import GenerationCreate, GenerationOut, SectionChange, StalenessOut
from app.services import document_service as svc

router = APIRouter(tags=["generations"])


@router.post("/generations", response_model=GenerationOut)
def create_generation(payload: GenerationCreate, db: Session = Depends(get_db)):
    try:
        generation = svc.create_generation(db, payload.selection_id)
        generation, data = svc.get_generation(db, generation.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        # Persist failed generation already; still return structured response if possible
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return _generation_out(db, generation, data)


@router.get("/generations/{generation_id}", response_model=GenerationOut)
def get_generation(generation_id: int, db: Session = Depends(get_db)):
    try:
        generation, data = svc.get_generation(db, generation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return _generation_out(db, generation, data)


def _generation_out(db: Session, generation, data: dict) -> GenerationOut:
    version = db.get(DocumentVersion, generation.version_id)
    return GenerationOut(
        id=generation.id,
        selection_id=generation.selection_id,
        version_id=generation.version_id,
        status=generation.status,
        model_name=generation.model_name,
        is_stale=generation.is_stale,
        stale_reason=generation.stale_reason,
        error_message=generation.error_message,
        created_at=generation.created_at,
        test_cases=data.get("test_cases", []),
        source_sections=[
            {
                "section_number": s.get("section_number"),
                "heading": s.get("heading"),
                "content_hash": s.get("content_hash"),
            }
            for s in data.get("source_sections", [])
        ],
        affected_document_version={
            "id": version.id,
            "version_label": version.version_label,
            "source_filename": version.source_filename,
        }
        if version
        else None,
    )


@router.get("/generations/{generation_id}/staleness", response_model=StalenessOut)
def generation_staleness(
    generation_id: int,
    against_version_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    try:
        result = svc.check_staleness(db, generation_id, against_version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return StalenessOut(
        generation_id=result["generation_id"],
        is_stale=result["is_stale"],
        reason=result["reason"],
        compared_against_version_id=result["compared_against_version_id"],
        changed_sections=[SectionChange(**c) for c in result["changed_sections"]],
    )
