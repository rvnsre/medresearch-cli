import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from medresearch.api import create_app
from medresearch.cli import app
from medresearch.config import ResearchError
from medresearch.reporting import markdown
from medresearch.runtime import workspace


def test_api_authenticated_end_to_end(settings, service):
    api = create_app(settings, service=service)
    headers = {"Authorization": "Bearer " + settings.api_token}
    with TestClient(api) as client:
        assert client.get("/health").status_code == 401
        assert client.get("/openapi.json").status_code == 401
        assert client.get("/health", headers=headers).json()["status"] == "ok"
        assert "/cases/{case_id}/reviews" in client.get("/openapi.json", headers=headers).json()["paths"]
        cid = client.post("/cases", json={"title": "Synthetic case"}, headers=headers).json()["id"]
        response = client.post(f"/cases/{cid}/documents", headers=headers,
                               files={"file": ("report.txt", b"Laboratory finding: synthetic value.")})
        assert response.status_code == 201
        doc = response.json()
        response = client.put(f"/cases/{cid}/documents/{doc['document_id']}", headers=headers,
                              json={"pages": doc["pages"]})
        assert response.status_code == 200
        job = client.post(f"/cases/{cid}/reviews", headers=headers,
                          json={"query": "laboratory finding", "provider": "none"}).json()
        assert job["state"] == "awaiting_review"
        assert client.post(f"/jobs/{job['id']}/research", headers=headers,
                           json={"digest": "0" * 64}).status_code == 409
        response = client.post(f"/jobs/{job['id']}/research", headers=headers, json={"digest": job["digest"]})
        assert response.status_code == 202
        assert client.get(f"/jobs/{job['id']}", headers=headers).json()["state"] == "completed"
        assert client.get(f"/jobs/{job['id']}/report", headers=headers).json()["articles"]
        md = client.get(f"/jobs/{job['id']}/report?format=markdown", headers=headers)
        assert md.headers["cache-control"] == "no-store"
        assert "Evidence table" in md.text
        assert client.delete(f"/cases/{cid}", headers=headers).status_code == 204
        assert client.get(f"/jobs/{job['id']}", headers=headers).status_code == 409


def test_api_boundaries_and_no_validation_echo(settings, service):
    headers = {"Authorization": "Bearer " + settings.api_token}
    with TestClient(create_app(settings, service=service)) as client:
        assert client.get("/health", headers={**headers, "Host": "evil.example"}).status_code == 400
        assert client.get("/health", headers={**headers, "Origin": "https://evil.example"}).status_code == 403
        result = client.post("/cases", headers=headers, json={"title": "X" * 201})
        assert result.status_code == 422 and "XXXX" not in result.text
        result = client.post("/cases", headers={**headers, "Content-Length": str(30 * 1024 * 1024)},
                             json={"title": "Case"})
        assert result.status_code == 413


def test_api_refuses_weak_token(settings):
    from dataclasses import replace

    with pytest.raises(ResearchError, match="random token"):
        create_app(replace(settings, api_token="weak"))


def test_workspace_lock_and_recovery(settings):
    with workspace(settings):
        with pytest.raises(ResearchError, match="already in use"), workspace(settings):
            pass


def test_cli_extract_review_prepare_and_delete(tmp_path, monkeypatch, service):
    monkeypatch.setenv("MEDRESEARCH_DATA_DIR", str(tmp_path / "cli-data"))
    monkeypatch.setattr("medresearch.runtime.Service", lambda settings: service)
    runner = CliRunner()
    created = runner.invoke(app, ["case", "create", "--title", "Synthetic"])
    assert created.exit_code == 0, created.output
    cid = json.loads(created.stdout)["id"]
    source = tmp_path / "synthetic.txt"
    source.write_text("Patient: Test Person\nLaboratory finding: synthetic value.", encoding="utf-8")
    added = runner.invoke(app, ["document", "add", cid, str(source)])
    assert added.exit_code == 0, added.output
    doc = json.loads(added.stdout)
    pages = tmp_path / "pages.json"
    pages.write_text(json.dumps(doc["pages"]), encoding="utf-8")
    result = runner.invoke(app, ["document", "review", cid, doc["document_id"], "--pages-file", str(pages)])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["review", cid, "--query", "synthetic", "--provider", "none"])
    assert result.exit_code == 0, result.output
    job = json.loads(result.stdout)
    assert job["state"] == "awaiting_review"
    result = runner.invoke(app, ["research", job["id"], "--approve", job["digest"]])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["state"] == "completed"
    export = tmp_path / "private-report.md"
    result = runner.invoke(app, ["report", job["id"], "--out", str(export)])
    assert result.exit_code == 0, result.output
    assert "Evidence table" in export.read_text(encoding="utf-8")
    assert runner.invoke(app, ["case", "delete", cid, "--yes"]).exit_code == 0


def test_markdown_neutralizes_untrusted_html():
    report = {"generated_at": "2026-01-01", "documents": [{"id": "doc", "name": "<script>alert(1)</script>",
               "pages": [{"number": 1, "method": "manual", "text": "[click](https://evil.invalid)\u001b[31m", "warnings": []}]}],
              "findings": [], "conflicts": [], "articles": [], "limitations": [], "questions": []}
    result = markdown(report)
    assert "<script>" not in result
    assert "[click](https://evil.invalid)" not in result
    assert "\u001b" not in result
