"""One side of the restart test, run in its own process so nothing survives in memory.

    python _restart_phase.py create <email>   -> prints the ids it created as JSON
    python _restart_phase.py verify <json>    -> exits 0 when everything is still there
"""

from __future__ import annotations

import base64
import json
import sys

import _path  # noqa: F401
from _pdf import make_pdf
from fastapi.testclient import TestClient

from omni_modal.api.app import create_app
from omni_modal.api.app import state as app_state

PASSWORD = "password123"
FACT = "The Halcyon warehouse lease runs until March 2031."


def _events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        name, data = "message", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if data:
            out.append((name, json.loads(data)))
    return out


def create(email: str) -> dict:
    with TestClient(create_app()) as client:
        token = client.post("/auth/register", json={"email": email, "password": PASSWORD}).json()["token"]
        auth = {"Authorization": f"Bearer {token}"}
        up = client.post("/ingest/upload", headers=auth, json={
            "filename": "lease.pdf", "title": "Halcyon lease", "content_base64": base64.b64encode(make_pdf(FACT)).decode()})
        assert up.status_code == 202, up.text
        doc = up.json()["document_id"]
        app_state.dispatcher.wait_idle()
        pid = client.post("/projects", headers=auth, json={"name": "Restart project"}).json()["id"]
        client.post(f"/projects/{pid}/documents", headers=auth, json={"document_id": doc})
        client.post(f"/projects/{pid}/items", headers=auth, json={"kind": "note", "title": "Check the lease"})
        events = _events(client.post("/query/stream", headers=auth,
                                     json={"question": "When does the Halcyon lease end?"}).text)
        sid = events[0][1]["session_id"]
        report = client.post(f"/projects/{pid}/reports", headers=auth).json()["id"]
        return {"email": email, "document_id": doc, "project_id": pid, "session_id": sid, "report_id": report}


def verify(ids: dict) -> list[str]:
    problems = []
    with TestClient(create_app()) as client:
        login = client.post("/auth/login", json={"email": ids["email"], "password": PASSWORD})
        if login.status_code != 200:
            return [f"sign-in failed after restart: {login.status_code}"]
        auth = {"Authorization": f"Bearer {login.json()['token']}"}

        doc = client.get(f"/documents/{ids['document_id']}", headers=auth)
        if doc.status_code != 200 or doc.json()["document"]["status"] != "ready":
            problems.append(f"document missing or not ready: {doc.status_code} {doc.text[:120]}")
        session = client.get(f"/research/sessions/{ids['session_id']}", headers=auth)
        if session.status_code != 200 or len(session.json().get("messages", [])) < 2:
            problems.append(f"research session missing: {session.status_code}")
        project = client.get(f"/projects/{ids['project_id']}", headers=auth)
        if project.status_code != 200:
            problems.append(f"project missing: {project.status_code}")
        else:
            body = project.json()
            if not body.get("items") or not body.get("documents"):
                problems.append("project lost its note or document link")
        if client.get(f"/reports/{ids['report_id']}", headers=auth).status_code != 200:
            problems.append("report missing")
        answer = client.post("/query", headers=auth, json={"question": "When does the Halcyon lease end?"}).json()
        if not any(c.get("document_id") == ids["document_id"] for c in answer.get("citations", [])):
            problems.append("research no longer finds the document's chunks")
    return problems


if __name__ == "__main__":
    if sys.argv[1] == "create":
        print("RESULT " + json.dumps(create(sys.argv[2])))
    else:
        found = verify(json.loads(sys.argv[2]))
        for p in found:
            print("PROBLEM " + p)
        sys.exit(1 if found else 0)
