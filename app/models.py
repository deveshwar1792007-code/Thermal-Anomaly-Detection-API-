
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Float,
    DateTime, ForeignKey, UniqueConstraint
)

from sqlalchemy.orm import relationship

from app.database import Base


class Asset(Base):
    __tablename__ = "assets"

    asset_id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)
    location = Column(String)

    warning_limit_C = Column(Float, nullable=False)
    critical_limit_C = Column(Float, nullable=False)

    inspections = relationship("Inspection", back_populates="asset")


class Inspection(Base):
    __tablename__ = "inspections"

    __table_args__ = (
        UniqueConstraint("asset_id", "timestamp"),
    )

    id = Column(Integer, primary_key=True, index=True)
    asset_id = Column(String, ForeignKey("assets.asset_id"), nullable=False)

    timestamp = Column(DateTime(timezone=True), nullable=False)
    max_temp_C = Column(Float, nullable=False)
    mean_temp_C = Column(Float, nullable=False)
    image_ref = Column(String)

    asset = relationship("Asset", back_populates="inspections")
    alert = relationship("Alert", back_populates="inspection", uselist=False)


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    asset_id = Column(String, ForeignKey("assets.asset_id"), nullable=False)

    inspection_id = Column(
        Integer,
        ForeignKey("inspections.id"),
        unique=True,
        nullable=False
    )

    severity = Column(String, nullable=False)
    status = Column(String, default="open", nullable=False)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )

    inspection = relationship("Inspection", back_populates="alert")
    acknowledgements = relationship(
        "AlertAcknowledgement",
        back_populates="alert"
    )


class AlertAcknowledgement(Base):
    __tablename__ = "alert_acknowledgements"

    id = Column(Integer, primary_key=True, index=True)
    alert_id = Column(Integer, ForeignKey("alerts.id"), nullable=False)

    acknowledged_by = Column(String, nullable=False)

    acknowledged_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )

    previous_status = Column(String, nullable=False)
    new_status = Column(String, nullable=False)

    alert = relationship("Alert", back_populates="acknowledgements")
