"""API-level tests using FastAPI TestClient (no Gemini calls)."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Use an isolated in-memory DB for tests
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["GENERATIONS_DIR"] = str(Path("data/generations_test").resolve())
os.environ["GEMINI_API_KEY"] = ""

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_ingest_list_search_compare_selection():
    v1 = Path("data/ct200_manual.pdf")
    v2 = Path("data/ct200_manual_v2.pdf")
    if not v1.exists():
        pytest.skip("assignment PDFs missing")

    r1 = client.post(
        "/documents/ingest",
        json={"file_path": str(v1), "document_key": "ct200_manual", "version_label": "v1"},
    )
    assert r1.status_code == 200, r1.text
    version_a = r1.json()["version"]["id"]
    assert r1.json()["nodes_created"] > 10

    # Immutable versions
    r_dup = client.post(
        "/documents/ingest",
        json={"file_path": str(v1), "document_key": "ct200_manual", "version_label": "v1"},
    )
    assert r_dup.status_code == 400

    r2 = client.post(
        "/documents/ingest",
        json={"file_path": str(v2), "document_key": "ct200_manual", "version_label": "v2"},
    )
    assert r2.status_code == 200, r2.text
    version_b = r2.json()["version"]["id"]

    sections = client.get(f"/versions/{version_a}/sections")
    assert sections.status_code == 200
    nodes = sections.json()
    assert any(n["section_number"] == "2.1.1.1" for n in nodes)
    # parent of skipped hierarchy
    deep = next(n for n in nodes if n["section_number"] == "2.1.1.1")
    parent = next(n for n in nodes if n["id"] == deep["parent_id"])
    assert parent["section_number"] == "2.1"

    detail = client.get(f"/nodes/{deep['id']}")
    assert detail.status_code == 200
    assert "battery" in detail.json()["body"].lower()

    search = client.get(f"/versions/{version_a}/search", params={"q": "overpressure"})
    assert search.status_code == 200
    assert len(search.json()) >= 1

    cmp = client.get(
        "/versions/compare",
        params={"version_a_id": version_a, "version_b_id": version_b},
    )
    assert cmp.status_code == 200
    body = cmp.json()
    assert any(c["section_number"] == "5.3" for c in body["added"])
    assert any(c["section_number"] == "3.2" for c in body["modified"])

    # Selection must be version-pinned
    node_ids = [n["id"] for n in nodes if n["section_number"] in {"3.2", "4.1"}]
    sel = client.post(
        "/selections",
        json={"version_id": version_a, "node_ids": node_ids, "name": "safety"},
    )
    assert sel.status_code == 200
    selection_id = sel.json()["id"]

    # Cross-version node ids rejected
    v2_sections = client.get(f"/versions/{version_b}/sections").json()
    bad = client.post(
        "/selections",
        json={
            "version_id": version_a,
            "node_ids": [v2_sections[0]["id"]],
            "name": "bad",
        },
    )
    assert bad.status_code == 400

    got = client.get(f"/selections/{selection_id}")
    assert got.status_code == 200
    assert len(got.json()["items"]) == 2

    # Persist a fake completed generation to exercise staleness without Gemini
    import json

    from app.models import Generation

    hashes = {item["section_number"]: item["content_hash"] for item in got.json()["items"]}
    db = TestingSessionLocal()
    gen_dir = Path(os.environ["GENERATIONS_DIR"])
    gen_dir.mkdir(parents=True, exist_ok=True)
    generation = Generation(
        selection_id=selection_id,
        version_id=version_a,
        status="completed",
        model_name="test-mock",
        json_path="",
        source_hashes_json=json.dumps(hashes),
    )
    db.add(generation)
    db.commit()
    db.refresh(generation)
    payload = {
        "generation_id": generation.id,
        "test_cases": [{"id": "TC-001", "title": "mock"}],
        "source_sections": got.json()["items"],
    }
    path = gen_dir / f"generation_{generation.id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    generation.json_path = str(path)
    db.commit()
    gid = generation.id
    db.close()

    stale = client.get(
        f"/generations/{gid}/staleness",
        params={"against_version_id": version_b},
    )
    assert stale.status_code == 200, stale.text
    body = stale.json()
    assert body["is_stale"] is True
    assert any(c["section_number"] == "3.2" for c in body["changed_sections"])

    retrieved = client.get(f"/generations/{gid}")
    assert retrieved.status_code == 200
    assert retrieved.json()["is_stale"] is True
    assert retrieved.json()["affected_document_version"]["id"] == version_a
