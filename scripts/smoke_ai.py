#!/usr/bin/env python3
import argparse
import io
import os
import uuid
from pathlib import Path

import httpx
from PIL import Image, ImageDraw


BASE_URL = os.environ.get(
    "ENERGIA_API_BASE_URL", "http://127.0.0.1:8000/api/v1"
).rstrip("/")
STATE_FILE = Path("/tmp/opencode/energia-ai-evidence-id")


def token(client):
    response = client.post(
        f"{BASE_URL}/auth/token/",
        json={"username": "maya", "password": "energia-demo"},
    )
    response.raise_for_status()
    return response.json()["access"]


def submit():
    with httpx.Client(timeout=45) as client:
        access = token(client)
        headers = {"Authorization": f"Bearer {access}"}
        tasks = client.get(f"{BASE_URL}/me/tasks/", headers=headers)
        tasks.raise_for_status()
        task = next(
            item
            for item in tasks.json()
            if item["task_kind"] == "energy_habit" and item["status"] == "due"
        )
        image = Image.new("RGB", (1000, 750), (30, 30, 28))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (190, 140, 810, 610),
            radius=30,
            fill=(216, 203, 179),
            outline=(47, 107, 255),
            width=12,
        )
        draw.rectangle((420, 300, 580, 450), fill=(47, 107, 255))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=88)
        response = client.post(
            f"{BASE_URL}/me/tasks/{task['id']}/evidence/",
            headers=headers,
            data={
                "media_type": "photo",
                "captured_at": "2026-11-21T12:00:00Z",
                "idempotency_key": f"live-ai-smoke-{uuid.uuid4()}",
            },
            files={"file": ("machine-panel.jpg", output.getvalue(), "image/jpeg")},
        )
        response.raise_for_status()
        evidence = response.json()
        STATE_FILE.write_text(evidence["id"])
        print(f"queued evidence={evidence['id']} task={task['rule_id']}")


def check():
    evidence_id = STATE_FILE.read_text().strip()
    with httpx.Client(timeout=30) as client:
        access = token(client)
        response = client.get(
            f"{BASE_URL}/evidence/{evidence_id}/",
            headers={"Authorization": f"Bearer {access}"},
        )
        response.raise_for_status()
        evidence = response.json()
        result = evidence.get("verification_result") or {}
        print(
            f"evidence={evidence_id} status={evidence['status']} "
            f"model={result.get('model_name', '')} "
            f"confidence={result.get('confidence', 0)}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["submit", "check"])
    args = parser.parse_args()
    submit() if args.action == "submit" else check()
