#!/usr/bin/env python3
import json
import os
import time
from pathlib import Path

import httpx


BASE_URL = os.environ.get(
    "ENERGIA_API_BASE_URL", "http://127.0.0.1:8000/api/v1"
).rstrip("/")
ROOT = Path(__file__).resolve().parents[1]
DEMO_BILL = ROOT.parent / "energia-frontend/assets/demo/classroom_energy_bill.png"
STATE_FILE = Path("/tmp/opencode/energia-admin-invoice-scan-id")


def main():
    with httpx.Client(timeout=120) as client:
        token_response = client.post(
            f"{BASE_URL}/auth/token/",
            json={
                "username": "energia-admin",
                "password": "Energia-Admin-Demo-2026!",
            },
        )
        token_response.raise_for_status()
        headers = {"Authorization": f"Bearer {token_response.json()['access']}"}
        context = client.get(f"{BASE_URL}/me/", headers=headers)
        context.raise_for_status()
        schema_response = client.get(f"{BASE_URL}/invoice-schemas/", headers=headers)
        schema_response.raise_for_status()
        schema = schema_response.json()[0]
        with DEMO_BILL.open("rb") as page:
            response = client.post(
                f"{BASE_URL}/invoice-scans/",
                headers=headers,
                data={
                    "site_id": context.json()["site"]["id"],
                    "schema_id": schema["id"],
                    "supplier": "NorthGrid Energy",
                    "rotations": json.dumps([0]),
                },
                files={"pages": (DEMO_BILL.name, page, "image/png")},
            )
        response.raise_for_status()
        scan = response.json()
        STATE_FILE.write_text(scan["id"])
        for _ in range(60):
            if scan["status"] not in {"uploaded", "processing"}:
                break
            time.sleep(1)
            detail = client.get(
                f"{BASE_URL}/invoice-scans/{scan['id']}/", headers=headers
            )
            detail.raise_for_status()
            scan = detail.json()
        print(
            f"scan={scan['id']} status={scan['status']} model={scan['model_name']} "
            f"fields={sorted(scan['raw_fields'])}"
        )


if __name__ == "__main__":
    main()
