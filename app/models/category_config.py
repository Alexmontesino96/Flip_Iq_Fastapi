"""Models for category-based configuration system.

Three-level config: global defaults → category overrides → channel overrides.
"""

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import relationship

from app.database import Base


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True)
    slug = Column(String(50), unique=True, nullable=False, index=True)
    display_name = Column(String(100), nullable=False)
    ebay_category_ids = Column(ARRAY(Integer), server_default="{}")
    is_active = Column(Boolean, server_default="true", nullable=False)
    observation_mode = Column(Boolean, server_default="false", nullable=False)
    engine_defaults = Column(JSONB, server_default="{}", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    channels = relationship("CategoryChannel", back_populates="category", cascade="all, delete-orphan")
    fee_schedules = relationship("FeeSchedule", back_populates="category", cascade="all, delete-orphan")
    shipping_templates = relationship("ShippingTemplate", back_populates="category", cascade="all, delete-orphan")


class CategoryChannel(Base):
    __tablename__ = "category_channels"
    __table_args__ = (
        UniqueConstraint("category_id", "channel", name="uq_category_channel"),
    )

    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("categories.id", ondelete="CASCADE"), nullable=False)
    channel = Column(String(50), nullable=False)
    is_enabled = Column(Boolean, server_default="true", nullable=False)
    engine_overrides = Column(JSONB, server_default="{}", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    category = relationship("Category", back_populates="channels")


class FeeSchedule(Base):
    __tablename__ = "fee_schedules"

    id = Column(Integer, primary_key=True)
    channel = Column(String(50), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id", ondelete="CASCADE"), nullable=True)
    fee_rate = Column(Numeric(6, 4), nullable=False)
    fee_fixed = Column(Numeric(8, 2), server_default="0", nullable=False)
    fee_note = Column(Text, nullable=True)
    price_min = Column(Numeric(10, 2), nullable=True)
    price_max = Column(Numeric(10, 2), nullable=True)
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    category = relationship("Category", back_populates="fee_schedules")


class ShippingTemplate(Base):
    __tablename__ = "shipping_templates"

    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("categories.id", ondelete="CASCADE"), nullable=False)
    channel = Column(String(50), nullable=True)
    label = Column(String(100), nullable=False)
    shipping_cost = Column(Numeric(8, 2), nullable=False)
    packaging_cost = Column(Numeric(8, 2), server_default="0", nullable=False)
    is_default = Column(Boolean, server_default="false", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    category = relationship("Category", back_populates="shipping_templates")
