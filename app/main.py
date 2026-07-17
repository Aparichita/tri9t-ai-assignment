from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.config import settings
from app.database import init_db
from app.routers import documents, generations, selections


@asynccontextmanager
async def lifespan(_: FastAPI):
    Path(settings.generations_dir).mkdir(parents=True, exist_ok=True)
    init_db()
    yield


app = FastAPI(
    title="CardioTrack Document Intelligence API",
    description=(
        "MedTech document hierarchy ingestion, versioning, section selection, "
        "and Gemini-powered QA test case generation with staleness detection."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(documents.router)
app.include_router(selections.router)
app.include_router(generations.router)


@app.get("/health")
def health():
    return {"status": "ok"}
