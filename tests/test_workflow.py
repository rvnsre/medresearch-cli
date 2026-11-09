import json
import time
from dataclasses import replace

import httpx
import pytest

from medresearch.ai import AI
from medresearch.config import ResearchError
from medresearch.models import Analysis, Conflict, Finding, Page, ReviewRequest
from medresearch.reporting import markdown
from medresearch.service import Service


def test_offline_until_exact_approval(service, reviewed_case):
    case_id, _ = reviewed_case
    calls = []
    original = service.pubmed.search
    service.pubmed.search = lambda *args: calls.append(args) or original(*args)
    job = service.prepare(case_id, ReviewRequest(query="synthetic finding", provider="none"))
    assert not calls
    with pytest.raises(ResearchError, match="digest"):
        service.queue(job["id"], "0" * 64)
    assert not calls
    service.queue(job["id"], job["digest"])
    done = service.run(job["id"])
    assert done["state"] == "completed"
    assert len(calls) == 1
    assert done["report"]["articles"][0]["pmid"] == "12345"
    assert done["report"]["ai_performed"] is False
    assert "Patient: Test Person" in done["report"]["documents"][0]["pages"][0]["text"]
    with pytest.raises(ResearchError, match="single-use"):
        service.queue(job["id"], job["digest"])
    with pytest.raises(ResearchError, match="exactly once"):
        service.run(job["id"])


def test_document_review_required(service):
    cid = service.store.create("Case")["id"]
    service.add(cid, "s.txt", b"Unreviewed extraction")
    with pytest.raises(ResearchError, match="Review"):
        service.prepare(cid, ReviewRequest(query="test"))


def test_edit_revokes_approval_and_completed_report(service, reviewed_case):
    cid, did = reviewed_case
    job = service.prepare(cid, ReviewRequest(query="test", provider="none"))
    service.queue(job["id"], job["digest"])
    service.run(job["id"])
    assert service.report(job["id"])
    service.edit(cid, did, [Page(number=1, text="Corrected finding", method="manual")])
    assert service.store.job(job["id"])["state"] == "failed"
    with pytest.raises(ResearchError):
        service.report(job["id"])
    with pytest.raises(ResearchError):
        service.queue(job["id"], job["digest"])


def test_expiry_and_tamper_fail_closed(service, reviewed_case):
    job = service.prepare(reviewed_case[0], ReviewRequest(query="test"))
    with service.store.connect() as db:
        db.execute("UPDATE jobs SET expires=? WHERE id=?", (time.time() - 1, job["id"]))
    with pytest.raises(ResearchError, match="expired"):
        service.queue(job["id"], job["digest"])
    with service.store.connect() as db:
        db.execute("UPDATE jobs SET expires=?,preview='{}' WHERE id=?", (time.time() + 100, job["id"]))
    with pytest.raises(ResearchError, match="integrity"):
        service.queue(job["id"], job["digest"])


def test_running_case_cannot_change_or_delete(service, reviewed_case):
    cid, did = reviewed_case
    job = service.prepare(cid, ReviewRequest(query="test"))
    service.queue(job["id"], job["digest"])
    with pytest.raises(ResearchError, match="active"):
        service.edit(cid, did, [Page(number=1, text="Changed")])
    with pytest.raises(ResearchError, match="active"):
        service.store.delete(cid)
    service.store.recover()
    assert "restart" in service.store.job(job["id"])["error"]
    service.store.delete(cid)
    with service.store.connect() as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_delete_cascades_cache_and_documents(service, reviewed_case):
    cid, _ = reviewed_case
    job = service.prepare(cid, ReviewRequest(query="test", provider="none"))
    service.queue(job["id"], job["digest"])
    service.run(job["id"])
    service.store.delete(cid)
    with service.store.connect() as db:
        for table in ("documents", "jobs", "cache", "approvals"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_external_is_separate_exact_payload_approval(service, reviewed_case):
    settings = replace(service.settings, model="test-model", external_url="https://ai.example/v1",
                       external_key="secret-not-in-preview")
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                                                     "message": {"content": '{"findings":[],"conflicts":[]}'}}]})

    service.settings = settings
    service.ai = AI(settings, httpx.Client(transport=httpx.MockTransport(handler)))
    job = service.prepare(reviewed_case[0], ReviewRequest(query="synthetic", provider="external"))
    service.queue(job["id"], job["digest"])
    waiting = service.run(job["id"])
    assert waiting["state"] == "awaiting_review" and waiting["phase"] == "external_ai"
    assert not sent
    preview = waiting["preview"]
    assert "Test Person" not in json.dumps(preview)
    assert "secret-not-in-preview" not in json.dumps(preview)
    with pytest.raises(ResearchError, match="digest"):
        service.queue(job["id"], job["digest"])
    service.queue(job["id"], waiting["digest"])
    assert service.run(job["id"])["state"] == "completed"
    assert json.loads(sent[0].content) == preview["body"]
    assert str(sent[0].url) == preview["url"]
    assert sent[0].headers["authorization"] == "Bearer secret-not-in-preview"


def test_ai_unavailable_falls_back_to_sources(service, reviewed_case):
    service.settings = replace(service.settings, model="unavailable")

    class Unavailable:
        def analyze(self, *args):
            raise ResearchError("AI unavailable.")

    service.ai = Unavailable()
    job = service.prepare(reviewed_case[0], ReviewRequest(query="test"))
    service.queue(job["id"], job["digest"])
    result = service.run(job["id"])
    assert result["report"]["ai_performed"] is False
    assert result["report"]["articles"]
    assert "AI unavailable." in result["report"]["limitations"]


def test_pubmed_failure_records_safe_error(service, reviewed_case):
    def fail(*args):
        raise RuntimeError("PRIVATE PATIENT CONTENT AND API KEY")

    service.pubmed.search = fail
    job = service.prepare(reviewed_case[0], ReviewRequest(query="test", provider="none"))
    service.queue(job["id"], job["digest"])
    result = service.run(job["id"])
    assert result["state"] == "failed"
    assert "PRIVATE" not in json.dumps(result)


def test_page_numbering_cannot_be_silently_changed(service, reviewed_case):
    with pytest.raises(ResearchError, match="page numbers"):
        service.edit(*reviewed_case, [Page(number=2, text="Text")])


def test_restart_preserves_completed_jobs(service, reviewed_case):
    job = service.prepare(reviewed_case[0], ReviewRequest(query="test", provider="none"))
    service.queue(job["id"], job["digest"])
    service.run(job["id"])
    reopened = Service(service.settings)
    reopened.store.recover()
    assert reopened.report(job["id"])["job_id"] == job["id"]


def test_local_ai_report_keeps_only_resolvable_excerpts(service, reviewed_case):
    cid, did = reviewed_case
    service.edit(cid, did, [Page(number=1, text="Laboratory finding: positive.\nLaboratory finding: negative.",
                                method="manual")])
    service.settings = replace(service.settings, model="test-model")
    ref = f"{did}:p1"

    class MatchingModel:
        def analyze(self, provider, payload):
            return Analysis(findings=[
                Finding(doc_ref=ref, document_quote="Laboratory finding: positive.", pmid="12345",
                        evidence_quote="Synthetic study reports a laboratory finding."),
                Finding(doc_ref=ref, document_quote="Laboratory finding: positive.", pmid="99999",
                        evidence_quote="Invented quotation"),
            ], conflicts=[Conflict(left_ref=ref, left_quote="Laboratory finding: positive.",
                                   right_ref=ref, right_quote="Laboratory finding: negative.")])

    service.ai = MatchingModel()
    job = service.prepare(cid, ReviewRequest(query="synthetic"))
    service.queue(job["id"], job["digest"])
    report = service.run(job["id"])["report"]
    assert report["ai_performed"]
    assert len(report["findings"]) == 1 and len(report["conflicts"]) == 1
    assert any("Discarded 1" in note for note in report["limitations"])
    assert "PMID 12345" in markdown(report)
    assert "99999" not in markdown(report)
