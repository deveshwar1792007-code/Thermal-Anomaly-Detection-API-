import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app


@pytest.fixture()
def client():
    test_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()


def add_asset(client, asset_id="A-1", warning=70, critical=85):
    response = client.post("/assets", json={
        "asset_id": asset_id, "name": "Test motor", "type": "motor",
        "warning_limit_C": warning, "critical_limit_C": critical,
    })
    assert response.status_code == 201
    return response


def inspection(timestamp, max_temp, asset_id="A-1"):
    return {
        "asset_id": asset_id, "timestamp": timestamp,
        "max_temp_C": max_temp, "mean_temp_C": min(max_temp, 60),
        "image_ref": "frames/test.csv",
    }


def test_warning_and_critical_alerts_created_at_thresholds(client):
    add_asset(client)
    warning = client.post("/inspections", json=inspection("2026-11-04T09:00:00Z", 70))
    critical = client.post("/inspections", json=inspection("2026-11-04T09:01:00Z", 85))
    assert warning.status_code == 201 and warning.json()["alert_severity"] == "warning"
    assert critical.status_code == 201 and critical.json()["alert_severity"] == "critical"


def test_normal_reading_creates_no_alert(client):
    add_asset(client)
    response = client.post("/inspections", json=inspection("2026-11-04T09:00:00Z", 69.9))
    assert response.json()["anomaly_detected"] is False
    assert client.get("/alerts").json() == []


def test_unknown_asset_returns_404(client):
    response = client.post("/inspections", json=inspection("2026-11-04T09:00:00Z", 80, "missing"))
    assert response.status_code == 404


def test_identical_retry_is_idempotent_and_changed_retry_conflicts(client):
    add_asset(client)
    body = inspection("2026-11-04T09:00:00+05:30", 80)
    first = client.post("/inspections", json=body)
    retry = client.post("/inspections", json=body)
    assert first.status_code == retry.status_code == 201
    assert first.json()["inspection"]["id"] == retry.json()["inspection"]["id"]
    assert retry.json()["duplicate"] is True
    changed = {**body, "max_temp_C": 81}
    assert client.post("/inspections", json=changed).status_code == 409
    assert len(client.get("/inspections").json()) == 1
    assert len(client.get("/alerts").json()) == 1


def test_acknowledgement_updates_status_and_records_audit(client):
    add_asset(client)
    created = client.post("/inspections", json=inspection("2026-11-04T09:00:00Z", 70))
    alert_id = client.get("/alerts").json()[0]["id"]
    response = client.post(f"/alerts/{alert_id}/acknowledge", json={"acknowledged_by": "operator"})
    assert response.status_code == 200
    assert response.json()["new_status"] == "acknowledged"
    assert client.get("/alerts").json() == []
    acknowledged = client.get("/alerts?status=acknowledged").json()
    assert len(acknowledged) == 1
    assert acknowledged[0]["acknowledgements"][0]["acknowledged_by"] == "operator"
    assert acknowledged[0]["acknowledgements"][0]["acknowledged_at"]
    assert created.status_code == 201


def test_asset_inspections_are_newest_first(client):
    add_asset(client)
    client.post("/inspections", json=inspection("2026-11-04T09:00:00Z", 60))
    client.post("/inspections", json=inspection("2026-11-04T10:00:00Z", 61))
    records = client.get("/assets/A-1/inspections").json()
    assert [row["max_temp_C"] for row in records] == [61, 60]
