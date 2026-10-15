from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlmodel import Session, SQLModel, create_engine, select

from . import models  # noqa: F401  (register tables)
from .claims import Claim
from .config import DATA_DIR, ensure_dirs
from .vault import Vault

ensure_dirs()
engine = create_engine(f"sqlite:///{DATA_DIR / 'dfir.sqlite3'}",
                       connect_args={"check_same_thread": False, "timeout": 30})


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        _add_missing_columns(conn)
        conn.commit()


def _add_missing_columns(conn) -> None:
    """Tiny forward-only migration: add columns that exist in the models but not yet in SQLite."""
    for table in SQLModel.metadata.sorted_tables:
        have = {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info('{table.name}')").fetchall()}
        for col in table.columns:
            if col.name in have:
                continue
            ddl_type = col.type.compile(dialect=engine.dialect)
            default = ""
            if col.default is not None and getattr(col.default, "is_scalar", False):
                v = col.default.arg
                default = f" DEFAULT {int(v) if isinstance(v, bool) else repr(v)}"
            conn.exec_driver_sql(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl_type}{default}')


@contextmanager
def session() -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as s:
        yield s


def get_session() -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as s:
        yield s


def case_dir(case_id: str) -> Path:
    d = DATA_DIR / "cases" / case_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def vault_for(case_id: str) -> Vault:
    return Vault(case_dir(case_id))


def save_claims(s: Session, claims: list[Claim]) -> None:
    for c in claims:
        s.merge(models.ClaimRow(id=c.id, case_id=c.case_id or "", media_id=c.media_id, stage=c.stage,
                                category=c.category, status=c.status.value, body=c.model_dump(mode="json")))


def load_claims(s: Session, case_id: str, stage: str | None = None, media_id: str | None = None) -> list[Claim]:
    q = select(models.ClaimRow).where(models.ClaimRow.case_id == case_id)
    if stage:
        q = q.where(models.ClaimRow.stage == stage)
    if media_id:
        q = q.where(models.ClaimRow.media_id == media_id)
    return [Claim(**r.body) for r in s.exec(q).all()]


def delete_claims(s: Session, case_id: str, stage: str, media_id: str | None = None) -> None:
    q = select(models.ClaimRow).where(models.ClaimRow.case_id == case_id, models.ClaimRow.stage == stage)
    if media_id:
        q = q.where(models.ClaimRow.media_id == media_id)
    for r in s.exec(q).all():
        s.delete(r)
