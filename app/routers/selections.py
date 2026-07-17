from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import SelectionCreate, SelectionItemOut, SelectionOut
from app.services import document_service as svc

router = APIRouter(tags=["selections"])


@router.post("/selections", response_model=SelectionOut)
def create_selection(payload: SelectionCreate, db: Session = Depends(get_db)):
    try:
        selection = svc.create_selection(
            db,
            version_id=payload.version_id,
            node_ids=payload.node_ids,
            name=payload.name,
        )
        selection = svc.get_selection(db, selection.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return SelectionOut(
        id=selection.id,
        version_id=selection.version_id,
        name=selection.name,
        created_at=selection.created_at,
        items=[
            SelectionItemOut(
                node_id=item.node.id,
                section_number=item.node.section_number,
                heading=item.node.heading,
                content_hash=item.node.content_hash,
            )
            for item in selection.items
        ],
    )


@router.get("/selections/{selection_id}", response_model=SelectionOut)
def get_selection(selection_id: int, db: Session = Depends(get_db)):
    try:
        selection = svc.get_selection(db, selection_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return SelectionOut(
        id=selection.id,
        version_id=selection.version_id,
        name=selection.name,
        created_at=selection.created_at,
        items=[
            SelectionItemOut(
                node_id=item.node.id,
                section_number=item.node.section_number,
                heading=item.node.heading,
                content_hash=item.node.content_hash,
            )
            for item in selection.items
        ],
    )
