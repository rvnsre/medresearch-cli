import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from medresearch.ai import AI, request_payload
from medresearch.api import create_app
from medresearch.boundary import LocalBoundary
from medresearch.config import ResearchError
from medresearch.exporting import write_report
from medresearch.network import bounded_request
from medresearch.pubmed import PubMed


def test_authentication_precedes_json_parsing(settings, service):
    with TestClient(create_app(settings, service=service)) as client:
        result = client.post("/cases", content=b"{invalid private document", headers={"Content-Type": "application/json"})
        assert result.status_code == 401
        assert result.headers["cache-control"] == "no-store"


def test_negative_content_length_rejected(settings, service):
    with TestClient(create_app(settings, service=service)) as client:
        result = client.get("/health", headers={"Authorization": "Bearer " + settings.api_token,
                                                "Content-Length": "-1"})
        assert result.status_code == 400


def test_service_url_trailing_slash_normalized(settings):
    normalized = replace(settings, ollama_url="http://127.0.0.1:11434/", external_url="https://ai.example/v1/")
    assert normalized.ollama_url == "http://127.0.0.1:11434"
    assert normalized.external_url == "https://ai.example/v1"


def test_private_data_rejected_in_any_git_checkout(settings, tmp_path):
    checkout = tmp_path / "another-project"
    (checkout / ".git").mkdir(parents=True)
    with pytest.raises(ResearchError, match="checkout"):
        replace(settings, data_dir=checkout / "patient-data")


class OversizedStream(httpx.SyncByteStream):
    def __init__(self):
        self.chunks_read = 0
        self.closed = False

    def __iter__(self):
        for _ in range(20):
            self.chunks_read += 1
            yield b"x" * 1_000_000

    def close(self):
        self.closed = True


def test_pubmed_size_limit_stops_network_read_early(service):
    stream = OversizedStream()
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream)))
    with pytest.raises(ResearchError, match="size limit"):
        PubMed(service.store, client)._get("esearch.fcgi", {})
    assert stream.chunks_read < 20 and stream.closed


def test_local_ollama_remote_alias_never_receives_patient_text(settings):
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "innocent:latest", "remote_model": "cloud-model",
                                                         "remote_host": "https://ollama.com"}]})
        return httpx.Response(200, json={"message": {"content": '{"findings":[],"conflicts":[]}'}})

    configured = replace(settings, model="innocent:latest")
    ai = AI(configured, httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ResearchError, match="remote|cloud"):
        ai.analyze("ollama", request_payload(configured, "ollama", {"doc:p1": "PRIVATE SYNTHETIC TEXT"}, []))
    assert all("PRIVATE SYNTHETIC TEXT" not in request.content.decode() for request in calls)


def test_streamed_request_limit_is_413_without_case_creation(settings, service):
    """Drive ASGI directly so the request has no Content-Length and arrives in chunks."""
    api = create_app(settings, service=service)
    chunks = iter([b" " * 1_000_000] * 23)
    sent = []

    async def receive():
        try:
            return {"type": "http.request", "body": next(chunks), "more_body": True}
        except StopIteration:
            return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/cases", "raw_path": b"/cases", "query_string": b"",
             "headers": [(b"host", b"localhost"), (b"content-type", b"application/json"),
                         (b"authorization", ("Bearer " + settings.api_token).encode())],
             "client": ("127.0.0.1", 12345), "server": ("127.0.0.1", 8765)}

    async def run():
        async with api.router.lifespan_context(api):
            await api(scope, receive, send)

    asyncio.run(run())
    assert next(message["status"] for message in sent if message["type"] == "http.response.start") == 413
    assert "private" not in json.dumps(sent, default=str)


def test_private_export_rejects_other_git_checkout(tmp_path):
    checkout = tmp_path / "another-project"
    (checkout / ".git").mkdir(parents=True)
    with pytest.raises(ResearchError, match="checkout"):
        write_report(checkout / "report.md", "Private synthetic record")
    assert not (checkout / "report.md").exists()


def test_failed_export_preserves_previous_report_and_removes_temp(tmp_path, monkeypatch):
    target = tmp_path / "report.md"
    target.write_text("Previous complete report", encoding="utf-8")

    def fail(*args):
        raise OSError("Disk error")

    monkeypatch.setattr("medresearch.exporting.os.replace", fail)
    with pytest.raises(OSError):
        write_report(target, "Replacement private report")
    assert target.read_text(encoding="utf-8") == "Previous complete report"
    assert list(tmp_path.glob(".medresearch-*.tmp")) == []


def test_successful_export_is_utf8_and_private(tmp_path):
    import os
    import stat

    target = tmp_path / "report.md"
    write_report(target, "Synthetic nəticə — Результат")
    assert "nəticə" in target.read_text(encoding="utf-8")
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.parametrize("url", ["https://ai.example:99999/v1", "https://[bad/v1", "https://ai.example/\nsecret"])
def test_invalid_urls_fail_with_safe_message(settings, url):
    with pytest.raises(ResearchError) as error:
        replace(settings, external_url=url)
    assert url not in str(error.value)


def test_redirect_does_not_forward_private_ai_body_or_key(settings):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(307, headers={"Location": "https://unexpected.example/collect"})

    configured = replace(settings, external_url="https://ai.example/v1", external_key="private-key", model="model")
    ai = AI(configured, httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True))
    with pytest.raises(ResearchError):
        ai.analyze("external", request_payload(configured, "external", {"doc:p1": "Private synthetic text"}, []))
    assert len(calls) == 1 and calls[0].url.host == "ai.example"


def test_unexpected_compression_is_rejected_before_reading():
    stream = OversizedStream()
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, headers={"Content-Encoding": "gzip"}, stream=stream,
    )))
    with pytest.raises(ResearchError, match="compressed"):
        bounded_request(client, "GET", "https://example.test", limit=100, label="Test")
    assert stream.chunks_read == 0 and stream.closed


@pytest.mark.parametrize("output", [[], {"choices": []}, {"choices": ["not an object"]},
                                   {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
                                   {"choices": [{"finish_reason": "stop", "message": None}]}])
def test_malformed_or_truncated_ai_envelopes_fail_safely(settings, output):
    configured = replace(settings, external_url="https://ai.example/v1", external_key="key", model="model")
    ai = AI(configured, httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=output))))
    with pytest.raises(ResearchError, match="invalid output"):
        ai.analyze("external", request_payload(configured, "external", {"doc:p1": "Synthetic private record"}, []))


def test_local_context_budget_prevents_chat_with_oversized_input(settings):
    calls = []
    configured = replace(settings, model="local:latest", ollama_context=4096)

    def handler(request):
        calls.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": configured.model}]})
        return httpx.Response(200, json={"model_info": {"llama.context_length": 8192}})

    ai = AI(configured, httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ResearchError, match="context budget"):
        ai.analyze("ollama", request_payload(configured, "ollama", {"doc:p1": "x" * 10_000}, []))
    assert len(calls) == 2 and all(request.url.path != "/api/chat" for request in calls)


@pytest.mark.parametrize("reason", ["stop", "length", None])
def test_local_model_chat_uses_explicit_verified_context(settings, reason):
    configured = replace(settings, model="cloud-research:latest", ollama_context=16384)
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": configured.model}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"model_info": {"llama.context_length": 8192}})
        return httpx.Response(200, json={"done": True, "done_reason": reason,
                                         "message": {"content": '{"findings":[],"conflicts":[]}'}})

    ai = AI(configured, httpx.Client(transport=httpx.MockTransport(handler)))
    payload = request_payload(configured, "ollama", {"doc:p1": "Synthetic"}, [])
    if reason == "stop":
        assert ai.analyze("ollama", payload).findings == []
    else:
        with pytest.raises(ResearchError, match="invalid output"):
            ai.analyze("ollama", payload)
    assert json.loads(calls[-1].content)["options"]["num_ctx"] == 8192


def test_pubmed_cannot_substitute_unrequested_article(service, reviewed_case):
    from conftest import XML

    from medresearch.models import ReviewRequest

    def handler(request):
        if request.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"idlist": ["99999"]}})
        return httpx.Response(200, text=XML)

    pubmed = PubMed(service.store, httpx.Client(transport=httpx.MockTransport(handler)))
    job = service.prepare(reviewed_case[0], ReviewRequest(query="synthetic", provider="none"))
    with pytest.raises(ResearchError, match="outside the approved"):
        pubmed.search(reviewed_case[0], job["preview"])


def test_missing_case_rejected_before_parser_runs(service, monkeypatch):
    monkeypatch.setattr("medresearch.service.extract", lambda *args: pytest.fail("Parser must not run"))
    with pytest.raises(ResearchError, match="not found"):
        service.add("nonexistent", "scan.pdf", b"untrusted input")


def test_slow_body_times_out_before_route_execution(settings):
    async def forbidden(*args):
        pytest.fail("Slow request must not reach application")

    boundary = LocalBoundary(forbidden, settings)
    boundary.body_timeout = 0.001
    sent = []

    async def receive():
        await asyncio.sleep(1)

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "headers": [(b"host", b"localhost"),
             (b"authorization", ("Bearer " + settings.api_token).encode())]}
    asyncio.run(boundary(scope, receive, send))
    assert sent[0]["status"] == 408
    assert (b"cache-control", b"no-store") in sent[0]["headers"]


def test_request_buffering_concurrency_is_bounded(settings):
    active = maximum = 0

    async def application(scope, receive, send):
        nonlocal active, maximum
        active += 1
        maximum = max(active, maximum)
        assert (await receive())["body"] == b""
        await asyncio.sleep(0.005)
        active -= 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    boundary = LocalBoundary(application, settings)
    scope = {"type": "http", "method": "GET", "headers": [(b"host", b"localhost"),
             (b"authorization", ("Bearer " + settings.api_token).encode())]}

    async def run():
        await asyncio.gather(*(boundary(scope, receive, send) for _ in range(9)))

    asyncio.run(run())
    assert maximum == 4 and active == 0


def test_unexpected_api_error_never_logs_private_exception(settings, service, monkeypatch, caplog):
    def fail(*args):
        raise RuntimeError("SYNTHETIC PRIVATE RECORD AND KEY")

    monkeypatch.setattr(service.store, "create", fail)
    with TestClient(create_app(settings, service=service)) as client:
        result = client.post("/cases", json={"title": "Synthetic"},
                             headers={"Authorization": "Bearer " + settings.api_token})
    assert result.status_code == 500
    assert result.headers["cache-control"] == "no-store"
    assert "SYNTHETIC PRIVATE" not in result.text + caplog.text
    assert "RuntimeError" in caplog.text


def test_unexpected_cli_error_never_prints_private_exception(tmp_path, service, monkeypatch):
    from typer.testing import CliRunner

    from medresearch.cli import app

    monkeypatch.setenv("MEDRESEARCH_DATA_DIR", str(tmp_path / "private"))
    monkeypatch.setattr("medresearch.runtime.Service", lambda settings: service)

    def fail(*args):
        raise RuntimeError("SYNTHETIC PRIVATE RECORD AND KEY")

    monkeypatch.setattr(service.store, "create", fail)
    result = CliRunner().invoke(app, ["case", "create", "--title", "Synthetic"])
    assert result.exit_code == 1
    assert "SYNTHETIC PRIVATE" not in result.output
    assert "RuntimeError" in result.output
