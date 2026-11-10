"""Load data/sample_data.json into a running API using its public endpoints."""
import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from pathlib import Path

BASE_URL = os.getenv("API_URL", "http://127.0.0.1:8000").rstrip("/")
DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "sample_data.json"


def post(path, payload):
    request = Request(
        f"{BASE_URL}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urlopen(request) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        if exc.code == 409 and path == "/assets":
            print(f"Asset already loaded: {payload['asset_id']}")
            return None
        raise RuntimeError(f"POST {path} failed ({exc.code}): {detail}") from exc


def main():
    data = json.loads(DATA_FILE.read_text())
    for asset in data["assets"]:
        post("/assets", asset)
    for inspection in data["inspections"]:
        result = post("/inspections", inspection)
        print(json.dumps(result, indent=2))
    with urlopen(f"{BASE_URL}/alerts?status=open") as response:
        print("Open alerts:")
        print(json.dumps(json.loads(response.read()), indent=2))


if __name__ == "__main__":
    main()
