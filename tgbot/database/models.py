from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    first_name: Mapped[str | None] = mapped_column(String(128))
    balance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    bot_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    free_searches_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    searches_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    last_active_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="user", lazy="selectin")
    search_tasks: Mapped[list["SearchTask"]] = relationship(back_populates="user", lazy="noload")

    @property
    def display_name(self) -> str:
        if self.username:
            return f"@{self.username}"
        return self.first_name or str(self.id)


class Tariff(Base):
    __tablename__ = "tariffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    price_rub: Mapped[int] = mapped_column(Integer, nullable=False)
    price_stars: Mapped[int] = mapped_column(Integer, nullable=False)
    max_tasks: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    check_interval: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    tariff_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("tariffs.id", ondelete="SET NULL"))
    tariff_name: Mapped[str] = mapped_column(String(64), nullable=False)
    max_tasks: Mapped[int] = mapped_column(Integer, nullable=False)
    check_interval: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    user: Mapped["User"] = relationship(back_populates="subscriptions")

    def is_active_at(self, moment: datetime) -> bool:
        return self.expires_at > moment


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    tariff_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("tariffs.id", ondelete="SET NULL"))
    amount_rub: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_native: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    external_id: Mapped[str | None] = mapped_column(String(128), index=True)
    invoice_url: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)


class SearchTask(Base):
    __tablename__ = "search_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    query: Mapped[str] = mapped_column(String(256), nullable=False)
    location_id: Mapped[int | None] = mapped_column(Integer)
    location_name: Mapped[str] = mapped_column(String(128), nullable=False)
    location_slug: Mapped[str | None] = mapped_column(String(128))
    price_min: Mapped[int | None] = mapped_column(Integer)
    price_max: Mapped[int | None] = mapped_column(Integer)
    wishes: Mapped[str | None] = mapped_column(Text)
    min_rating: Mapped[int] = mapped_column(Integer, default=6, nullable=False)
    check_interval: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(String(256))
    found_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notified_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    user: Mapped["User"] = relationship(back_populates="search_tasks")


class SeenListing(Base):
    __tablename__ = "seen_listings"
    __table_args__ = (UniqueConstraint("task_id", "listing_id", name="uq_seen_task_listing"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("search_tasks.id", ondelete="CASCADE"), index=True, nullable=False)
    listing_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class ListingEvaluation(Base):
    __tablename__ = "listing_evaluations"
    __table_args__ = (UniqueConstraint("listing_id", "request_hash", name="uq_eval_listing_request"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(32), nullable=False)
    rating: Mapped[float] = mapped_column(Float, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    listing_payload: Mapped[str | None] = mapped_column(Text)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
