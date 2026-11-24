from datetime import datetime
from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base
from src.utils.time import now


class RecallSignoff(Base):
    """已签核的混箱调拨单据，入库记录只能挂在已签核单据下。"""
    __tablename__ = 'recall_signoffs'

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True)
    title: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default='signed')
    run_id: Mapped[int | None] = mapped_column(ForeignKey('runs.id'))
    note: Mapped[str | None] = mapped_column(String(500))
    signed_by: Mapped[str] = mapped_column(String(64))
    signed_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(default=now)


class RecallEvent(Base):
    """按实物先后顺序重放的入库与调拨流水，调拨不记录批号。"""
    __tablename__ = 'recall_events'

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    location: Mapped[str | None] = mapped_column(String(40))
    batch_no: Mapped[str | None] = mapped_column(String(80))
    from_location: Mapped[str | None] = mapped_column(String(40))
    to_location: Mapped[str | None] = mapped_column(String(40))
    quantity: Mapped[int]
    signoff_id: Mapped[int | None] = mapped_column(ForeignKey('recall_signoffs.id'))
    request_key: Mapped[str] = mapped_column(String(100), unique=True)
    occurred_at: Mapped[datetime] = mapped_column(index=True)
    author: Mapped[str] = mapped_column(String(64))
    voided_at: Mapped[datetime | None]
    voided_by: Mapped[str | None] = mapped_column(String(64))
    void_reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=now)


class RecallVersion(Base):
    """每次登记生成的候选版本；库存不足的版本暂缓发布，不替代上一版。"""
    __tablename__ = 'recall_versions'

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[int] = mapped_column(unique=True)
    status: Mapped[str] = mapped_column(String(16), default='published', index=True)
    reason: Mapped[str | None] = mapped_column(String(500))
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)
    published_at: Mapped[datetime | None]


class RecallLedger(Base):
    """单行台账锁，串行化补录与版本发布。"""
    __tablename__ = 'recall_ledger'

    id: Mapped[int] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(default=0)
