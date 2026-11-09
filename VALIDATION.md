# Release validation — 0.1.1

Validation uses synthetic documents only. No real patient documents were used or published.

## Verified locally

- Windows, Python 3.11.9: editable package installation and dependency consistency.
- 67 passing tests and 90% measured statement coverage across the package.
- CLI/API, extraction, storage, approval, PubMed and AI-adapter integration tests.
- Real PubMed request from the installed CLI: three article records retrieved;
  Markdown and JSON reports exported, followed by local case deletion.
- Actual loopback API process: authenticated health/OpenAPI, document upload,
  reviewed page update, case deletion and rejection of an invalid bearer token.
- UTF-8 redirected CLI output, including non-ASCII synthetic document content.
- Static Ruff checks and Python source compilation.
- Source distribution and installable wheel builds.

The automated tests cover text PDF extraction, real PDF rendering with simulated
OCR availability/failure, image OCR with simulated Tesseract responses, corrupt
and encrypted files, XML entity rejection, bounded retrieval retries, caching,
document review, approval expiry/invalidation/replay, exact external AI request
matching, provenance filtering, prompt injection output rejection, restart recovery,
case deletion cascades, private error responses and Markdown escaping.
The audit adds authentication-before-parsing, chunked request limits, buffering
concurrency, slow-body timeout, bounded network reads, cloud-alias/context guards,
AI completion-envelope checks, Git checkout protection and atomic-export tests.
Unexpected API/CLI exception messages are also checked for private-data leakage.

## Explicit limits

- Tesseract and Ollama are not installed in the verification environment. Live
  OCR recognition quality and real model behavior have not been validated.
  Their missing-dependency behavior and HTTP/JSON adapter contracts are tested.
- External AI uses deterministic HTTP fixtures; no paid provider or real patient
  data was used. Provider compatibility depends on the configured endpoint/model.
- Windows/Linux Python 3.11/3.12 CI is configured; local execution covers Windows
  3.11. Hosted CI status is separate from local verification.
- Starlette emits a test-client deprecation warning for the current `httpx`
  compatibility path. It does not fail the tests or affect the runtime HTTP client.
- Exact quote checks establish traceable excerpts, not clinical truth, relevance,
  completeness, masking reliability or fitness for patient care. No clinical
  validation, regulatory compliance or medical-device status is claimed.

## Reproduce

```powershell
python -m pip install -e ".[dev]"
ruff check .
pytest --cov=medresearch --cov-report=term-missing
python -m build
```

`requirements-verified.txt` records the versions used for local verification,
without local paths or credentials. Use `pip install -c requirements-verified.txt
-e ".[dev]"` to constrain another installation to those versions.
