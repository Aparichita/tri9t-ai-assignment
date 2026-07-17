from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Document(Base):
    """Logical document identity shared across versions (never overwritten)."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    versions: Mapped[list["DocumentVersion"]] = relationship(back_populates="document")


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version_label", name="uq_doc_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    version_label: Mapped[str] = mapped_column(String(64))
    source_filename: Mapped[str] = mapped_column(String(512))
    source_path: Mapped[str] = mapped_column(String(1024))
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped["Document"] = relationship(back_populates="versions")
    nodes: Mapped[list["DocumentNode"]] = relationship(back_populates="version")


class DocumentNode(Base):
    """One section/heading node in a versioned document tree."""

    __tablename__ = "document_nodes"
    __table_args__ = (
        UniqueConstraint("version_id", "section_number", name="uq_version_section"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("document_nodes.id"), nullable=True)
    section_number: Mapped[str] = mapped_column(String(64), index=True)
    heading: Mapped[str] = mapped_column(String(512))
    level: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)

    version: Mapped["DocumentVersion"] = relationship(back_populates="nodes")
    parent: Mapped["DocumentNode | None"] = relationship(remote_side=[id])


class Selection(Base):
    """Version-pinned set of nodes chosen for QA generation."""

    __tablename__ = "selections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    items: Mapped[list["SelectionItem"]] = relationship(back_populates="selection")


class SelectionItem(Base):
    __tablename__ = "selection_items"
    __table_args__ = (UniqueConstraint("selection_id", "node_id", name="uq_selection_node"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    selection_id: Mapped[int] = mapped_column(ForeignKey("selections.id"), index=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("document_nodes.id"), index=True)

    selection: Mapped["Selection"] = relationship(back_populates="items")
    node: Mapped["DocumentNode"] = relationship()


class Generation(Base):
    """Metadata for a Gemini QA run. Full payload lives in a JSON file."""

    __tablename__ = "generations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    selection_id: Mapped[int] = mapped_column(ForeignKey("selections.id"), index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    model_name: Mapped[str] = mapped_column(String(128), default="")
    json_path: Mapped[str] = mapped_column(String(1024))
    # Snapshot of hashes used at generation time for staleness checks
    source_hashes_json: Mapped[str] = mapped_column(Text, default="{}")
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    stale_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    selection: Mapped["Selection"] = relationship()
    version: Mapped["DocumentVersion"] = relationship()
