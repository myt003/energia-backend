#!/usr/bin/env python3
import json
import os
import urllib.request


BASE_URL = os.environ.get("ENERGIA_API_BASE_URL", "http://127.0.0.1:8000/api/v1").rstrip("/")


def request(path, *, method="GET", token=None, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(
        urllib.request.Request(
            f"{BASE_URL}/{path.lstrip('/')}",
            data=body,
            headers=headers,
            method=method,
        ),
        timeout=30,
    ) as response:
        return json.load(response)


def main():
    tokens = request(
        "auth/token/",
        method="POST",
        payload={"username": "maya", "password": "energia-demo"},
    )
    token = tokens["access"]
    profile = request("me/work-profile/", token=token)
    assets = request("assets/", token=token)
    candidates = request("asset-candidates/", token=token)
    tasks = request("me/tasks/", token=token)
    assignments = [
        {
            "asset_id": item["asset"]["id"],
            "interactions": item["interactions"],
        }
        for item in profile["assignments"]
    ]
    request(
        "me/equipment/",
        method="PUT",
        token=token,
        payload={"assignments": assignments},
    )
    assert profile["employee"]["username"] == "maya"
    assert assets["count"] >= 9
    assert candidates["count"] >= 1
    assert {"due", "completed", "overdue", "waiting_review"} <= {
        task["status"] for task in tasks
    }
    print(
        f"OK {BASE_URL}: profile={profile['employee']['name']}, "
        f"assets={assets['count']}, candidates={candidates['count']}, tasks={len(tasks)}"
    )


if __name__ == "__main__":
    main()
