"""Products, versions, benefits, nutrients and growing schedules."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from app.models.enums import (
    AgeGroup,
    IrrigationFrequency,
    ProductStatus,
    RepeatRule,
    TaskType,
    TimeOfDay,
)


class Product(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("food_item_id", name="uq_products_food_item"),
        Index("ix_products_status", "status"),
    )

    food_item_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("food_items.id", ondelete="SET NULL")
    )
    name_en: Mapped[str] = mapped_column(String(160), nullable=False)
    name_rw: Mapped[str | None] = mapped_column(String(160))
    photo_url: Mapped[str | None] = mapped_column(String(500))
    description_en: Mapped[str | None] = mapped_column(Text)
    description_rw: Mapped[str | None] = mapped_column(Text)

    status: Mapped[ProductStatus] = mapped_column(
        value_enum(ProductStatus, "product_status", 16),
        nullable=False,
        default=ProductStatus.DRAFT,
        server_default=ProductStatus.DRAFT.value,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    how_not_to_use: Mapped[str | None] = mapped_column(Text)
    how_to_preserve: Mapped[str | None] = mapped_column(Text)
    conditions_diseases: Mapped[str | None] = mapped_column(Text)
    conditions_note_translation_key: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default="products.health.note",
        server_default="products.health.note",
    )

    reviewer_name: Mapped[str | None] = mapped_column(String(120))
    reviewer_organisation: Mapped[str | None] = mapped_column(String(160))
    review_date: Mapped[date | None] = mapped_column(Date)
    needs_expert_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    growing_steps_validated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    time_to_harvest_min_days: Mapped[int | None] = mapped_column(Integer)
    time_to_harvest_max_days: Mapped[int | None] = mapped_column(Integer)
    harvest_method: Mapped[str | None] = mapped_column(Text)

    irrigation_method: Mapped[str | None] = mapped_column(Text)
    irrigation_frequency: Mapped[IrrigationFrequency | None] = mapped_column(
        value_enum(IrrigationFrequency, "irrigation_frequency", 16)
    )
    irrigation_time_of_day: Mapped[TimeOfDay | None] = mapped_column(
        value_enum(TimeOfDay, "time_of_day", 16)
    )

    place_type: Mapped[str | None] = mapped_column(String(60))
    temperature_min_c: Mapped[float | None] = mapped_column(Numeric(5, 1))
    temperature_max_c: Mapped[float | None] = mapped_column(Numeric(5, 1))
    humidity_min_pct: Mapped[float | None] = mapped_column(Numeric(5, 1))
    humidity_max_pct: Mapped[float | None] = mapped_column(Numeric(5, 1))

    resources: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    recipes: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    season_suitability: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    spray_details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    benefits: Mapped[list[ProductBenefit]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    nutrients: Mapped[list[ProductNutrient]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    schedule_tasks: Mapped[list[ProductScheduleTask]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductScheduleTask.day_offset",
    )
    versions: Mapped[list[ProductVersion]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductVersion(Base, UUIDPrimaryKeyMixin):
    """A snapshot of a product taken whenever a trackable product changes."""

    __tablename__ = "product_versions"
    __table_args__ = (UniqueConstraint("product_id", "version", name="uq_product_versions"),)

    product_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("admins.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    product: Mapped[Product] = relationship(back_populates="versions")


class ProductBenefit(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "product_benefits"
    __table_args__ = (UniqueConstraint("product_id", "age_group", name="uq_product_benefits"),)

    product_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    age_group: Mapped[AgeGroup] = mapped_column(
        value_enum(AgeGroup, "age_group", 20), nullable=False
    )
    benefit_text: Mapped[str | None] = mapped_column(Text)

    product: Mapped[Product] = relationship(back_populates="benefits")


class ProductNutrient(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "product_nutrients"
    __table_args__ = (UniqueConstraint("product_id", "nutrient", name="uq_product_nutrients"),)

    product_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    nutrient: Mapped[str] = mapped_column(String(60), nullable=False)
    amount_per_100g: Mapped[float | None] = mapped_column(Numeric(14, 4))
    unit: Mapped[str | None] = mapped_column(String(20))
    source: Mapped[str | None] = mapped_column(Text)

    product: Mapped[Product] = relationship(back_populates="nutrients")


class ProductScheduleTask(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "product_schedule_tasks"
    __table_args__ = (Index("ix_product_schedule_tasks_product", "product_id", "day_offset"),)

    product_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    task_type: Mapped[TaskType] = mapped_column(
        value_enum(TaskType, "task_type", 24), nullable=False
    )
    day_offset: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    repeat_rule: Mapped[RepeatRule] = mapped_column(
        value_enum(RepeatRule, "repeat_rule", 16),
        nullable=False,
        default=RepeatRule.ONCE,
        server_default=RepeatRule.ONCE.value,
    )
    repeat_weekday: Mapped[int | None] = mapped_column(Integer)
    repeat_interval_days: Mapped[int | None] = mapped_column(Integer)
    repeat_until_day: Mapped[int | None] = mapped_column(Integer)
    time_of_day: Mapped[TimeOfDay] = mapped_column(
        value_enum(TimeOfDay, "time_of_day", 16),
        nullable=False,
        default=TimeOfDay.MORNING,
        server_default=TimeOfDay.MORNING.value,
    )
    instruction_text: Mapped[str] = mapped_column(Text, nullable=False)
    instruction_translation_key: Mapped[str | None] = mapped_column(String(120))
    reminder_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    product: Mapped[Product] = relationship(back_populates="schedule_tasks")
