from datetime import datetime, timezone

from fastapi import FastAPI, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.database import Base, engine, get_db
from app import models

# Create tables for a fresh database. In production, use a migration tool.
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Thermal Anomaly Detection API",
    description="Backend for industrial thermal inspection and anomaly alerts",
    version="1.1.0",
)


class AssetCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    asset_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    location: str | None = None
    warning_limit_C: float = Field(gt=0, allow_inf_nan=False)
    critical_limit_C: float = Field(gt=0, allow_inf_nan=False)


class InspectionCreate(BaseModel):
    asset_id: str = Field(min_length=1)
    timestamp: datetime
    max_temp_C: float = Field(allow_inf_nan=False)
    mean_temp_C: float = Field(allow_inf_nan=False)
    image_ref: str | None = None


class AcknowledgeRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    acknowledged_by: str = Field(min_length=1)


def normalize_timestamp(value: datetime) -> datetime:
    """Normalize timestamps to naive UTC for consistent SQLite comparisons."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def timestamp_to_iso(value: datetime) -> str:
    # SQLite drops timezone metadata, so stored naive values are interpreted as UTC.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def asset_to_dict(asset):
    return {
        "asset_id": asset.asset_id,
        "name": asset.name,
        "type": asset.type,
        "location": asset.location,
        "warning_limit_C": asset.warning_limit_C,
        "critical_limit_C": asset.critical_limit_C,
    }


def inspection_to_dict(inspection):
    return {
        "id": inspection.id,
        "asset_id": inspection.asset_id,
        "timestamp": timestamp_to_iso(inspection.timestamp),
        "max_temp_C": inspection.max_temp_C,
        "mean_temp_C": inspection.mean_temp_C,
        "image_ref": inspection.image_ref,
    }


def alert_to_dict(alert):
    return {
        "id": alert.id,
        "asset_id": alert.asset_id,
        "inspection_id": alert.inspection_id,
        "severity": alert.severity,
        "status": alert.status,
        "created_at": timestamp_to_iso(alert.created_at) if alert.created_at else None,
        "acknowledgements": [
            {
                "acknowledged_by": item.acknowledged_by,
                "acknowledged_at": timestamp_to_iso(item.acknowledged_at),
                "previous_status": item.previous_status,
                "new_status": item.new_status,
            }
            for item in sorted(alert.acknowledgements, key=lambda row: row.acknowledged_at or datetime.min)
        ],
    }


@app.get("/")
def home():
    return {"message": "Thermal Anomaly Detection API is running", "status": "success"}


@app.post("/assets", status_code=201)
def create_asset(asset_data: AssetCreate, db: Session = Depends(get_db)):
    if asset_data.warning_limit_C >= asset_data.critical_limit_C:
        raise HTTPException(status_code=400, detail="Critical limit must be greater than warning limit")
    asset = models.Asset(**asset_data.model_dump())
    db.add(asset)
    try:
        db.commit()
        db.refresh(asset)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Asset ID already exists")
    return asset_to_dict(asset)


@app.get("/assets")
def get_assets(db: Session = Depends(get_db)):
    return [asset_to_dict(asset) for asset in db.query(models.Asset).order_by(models.Asset.asset_id).all()]


@app.post("/inspections", status_code=201)
def create_inspection(inspection_data: InspectionCreate, db: Session = Depends(get_db)):
    asset = db.query(models.Asset).filter(models.Asset.asset_id == inspection_data.asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    if inspection_data.mean_temp_C > inspection_data.max_temp_C:
        raise HTTPException(status_code=400, detail="Mean temperature cannot exceed maximum temperature")

    values = inspection_data.model_dump()
    normalized_timestamp = normalize_timestamp(values["timestamp"])
    existing = db.query(models.Inspection).filter(
        models.Inspection.asset_id == values["asset_id"],
        models.Inspection.timestamp == normalized_timestamp,
    ).first()
    if existing:
        if (existing.max_temp_C, existing.mean_temp_C, existing.image_ref) != (
            values["max_temp_C"], values["mean_temp_C"], values["image_ref"]
        ):
            raise HTTPException(status_code=409, detail="Inspection timestamp already exists with different data")
        alert = db.query(models.Alert).filter(models.Alert.inspection_id == existing.id).first()
        severity = alert.severity if alert else None
        return {
            "inspection": inspection_to_dict(existing),
            "anomaly_detected": severity is not None,
            "alert_severity": severity,
            "message": f"{severity.capitalize()} alert generated" if severity else "Normal temperature. No alert generated.",
            "duplicate": True,
        }

    inspection = models.Inspection(
        asset_id=values["asset_id"], timestamp=normalized_timestamp,
        max_temp_C=values["max_temp_C"], mean_temp_C=values["mean_temp_C"],
        image_ref=values["image_ref"],
    )
    db.add(inspection)
    try:
        db.flush()
        severity = (
            "critical" if inspection.max_temp_C >= asset.critical_limit_C
            else "warning" if inspection.max_temp_C >= asset.warning_limit_C
            else None
        )
        if severity:
            db.add(models.Alert(asset_id=asset.asset_id, inspection_id=inspection.id, severity=severity, status="open"))
        db.commit()
        db.refresh(inspection)
    except IntegrityError:
        db.rollback()
        # Another concurrent request may have inserted this same inspection.
        duplicate = db.query(models.Inspection).filter(
            models.Inspection.asset_id == values["asset_id"],
            models.Inspection.timestamp == normalized_timestamp,
        ).first()
        if duplicate and (duplicate.max_temp_C, duplicate.mean_temp_C, duplicate.image_ref) == (
            values["max_temp_C"], values["mean_temp_C"], values["image_ref"]
        ):
            alert = db.query(models.Alert).filter(models.Alert.inspection_id == duplicate.id).first()
            severity = alert.severity if alert else None
            return {
                "inspection": inspection_to_dict(duplicate), "anomaly_detected": severity is not None,
                "alert_severity": severity,
                "message": f"{severity.capitalize()} alert generated" if severity else "Normal temperature. No alert generated.",
                "duplicate": True,
            }
        raise HTTPException(status_code=409, detail="Inspection timestamp already exists with different data")

    return {
        "inspection": inspection_to_dict(inspection),
        "anomaly_detected": severity is not None,
        "alert_severity": severity,
        "message": f"{severity.capitalize()} alert generated" if severity else "Normal temperature. No alert generated.",
        "duplicate": False,
    }


@app.get("/inspections")
def get_inspections(
    asset_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    query = db.query(models.Inspection)
    if asset_id is not None:
        query = query.filter(models.Inspection.asset_id == asset_id)
    inspections = query.order_by(models.Inspection.timestamp.desc(), models.Inspection.id.desc()).limit(limit).all()
    return [inspection_to_dict(item) for item in inspections]


@app.get("/assets/{asset_id}/inspections")
def get_asset_inspections(
    asset_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    if not db.query(models.Asset).filter(models.Asset.asset_id == asset_id).first():
        raise HTTPException(status_code=404, detail="Asset not found")
    inspections = db.query(models.Inspection).filter(
        models.Inspection.asset_id == asset_id
    ).order_by(models.Inspection.timestamp.desc(), models.Inspection.id.desc()).limit(limit).all()
    return [inspection_to_dict(item) for item in inspections]


@app.get("/alerts")
def get_alerts(status: str = "open", db: Session = Depends(get_db)):
    if status not in {"open", "acknowledged"}:
        raise HTTPException(status_code=400, detail="Invalid alert status")
    alerts = db.query(models.Alert).filter(models.Alert.status == status).order_by(models.Alert.created_at.desc()).all()
    return [alert_to_dict(alert) for alert in alerts]


@app.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: int, request: AcknowledgeRequest, db: Session = Depends(get_db)):
    alert = db.query(models.Alert).filter(models.Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    if alert.status != "open":
        raise HTTPException(status_code=409, detail="Alert has already been acknowledged")
    previous_status = alert.status
    alert.status = "acknowledged"
    acknowledged_at = datetime.now(timezone.utc)
    db.add(models.AlertAcknowledgement(
        alert_id=alert.id, acknowledged_by=request.acknowledged_by,
        acknowledged_at=acknowledged_at, previous_status=previous_status,
        new_status="acknowledged",
    ))
    db.commit()
    db.refresh(alert)
    return {
        "message": "Alert acknowledged successfully", "alert_id": alert.id,
        "previous_status": previous_status, "new_status": alert.status,
        "acknowledged_by": request.acknowledged_by,
        "acknowledged_at": acknowledged_at.isoformat(),
    }


@app.get("/health")
def health_check():
    return {"status": "healthy", "application": "Thermal Anomaly Detection API"}
