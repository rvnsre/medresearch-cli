# MedResearch — operations guide

[← Back to the project overview](../README.md)

Detailed configuration, commands and operational limits. Examples are documentation; review placeholders and private-data paths before running them.

## Windows, Linux and macOS

The same Python package and CLI/API run on all three operating systems.
CI tests and builds Python 3.11/3.12 on Windows, Linux and macOS.

On Linux and macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -c requirements-verified.txt -e '.[dev]'
.venv/bin/medresearch doctor
export MEDRESEARCH_API_TOKEN="$(.venv/bin/medresearch token)"
.venv/bin/medresearch serve
```

Install Tesseract and the aze/rus/eng language data through your operating system's
package manager or the official Tesseract installation guidance linked below.
Ollama is optional and is configured the same way on each platform.
Private data defaults to `%LOCALAPPDATA%/MedResearch` on Windows,
`~/Library/Application Support/MedResearch` on macOS, and
`$XDG_DATA_HOME/MedResearch` (or `~/.local/share/MedResearch`) on Linux.
`MEDRESEARCH_DATA_DIR` overrides these defaults.

## Install on Windows

Python 3.11+ is required. From the checkout:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c requirements-verified.txt -e ".[dev]"
# Activate if your local policy permits; activation is optional.
# .\.venv\Scripts\Activate.ps1
.\.venv\Scripts\medresearch.exe doctor
```

The examples below use `medresearch` with the environment activated. Without
activation use `.\.venv\Scripts\medresearch.exe` instead. Linux/macOS:
`python3 -m venv .venv`, then `.venv/bin/python -m pip install -c requirements-verified.txt -e ".[dev]"`.

For OCR, install Tesseract from a trusted distribution and install `aze`, `rus`
and `eng` trained-data files in its tessdata directory. See the official
[Tesseract installation guidance](https://tesseract-ocr.github.io/tessdoc/Installation.html)
and [language data](https://github.com/tesseract-ocr/tessdata).
If the executable is not on PATH, set its absolute path:

```powershell
$env:MEDRESEARCH_TESSERACT_CMD = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
$env:MEDRESEARCH_OCR_LANGUAGES = 'aze+rus+eng'
```

Missing language packs are reported. Available requested languages can still be
used, but all OCR text needs verification, particularly numbers and negation.
If OCR is unavailable, blank pages retain warnings and can be filled manually.
Handwriting and medical imagery are not reliably supported.

For local AI, install [Ollama](https://ollama.com/download), pull a model suitable
for your machine and set its exact installed model name. The application does not
download models automatically or assume hardware capacity.

```powershell
$env:MEDRESEARCH_MODEL = 'your-installed-model:tag'
medresearch doctor
```

The default Ollama endpoint is `http://127.0.0.1:11434`. Only loopback HTTP is
accepted for the local adapter; remote/cloud Ollama must use the external path.
Before any document text is sent, the local adapter inspects `/api/tags` and
`/api/show`. Remote/cloud aliases, missing models and unverifiable context
metadata are rejected. A loopback address by itself does not make a model local.
For sensitive work, disable cloud on the separate Ollama server using
`OLLAMA_NO_CLOUD=1` before starting that server, as described in the official
[Ollama FAQ](https://github.com/ollama/ollama/blob/main/docs/faq.mdx).
Setting that variable only in this client does not reconfigure an already-running server.
`doctor` checks configured model availability and OCR dependencies. You can use
`--provider none` without either OCR (for text files/PDFs) or a model.

## CLI walkthrough

Keep the data directory and exports outside the public source checkout. The
platform defaults are listed in [the platform notes](../README.md#platforms-and-requirements). For a custom location:

```powershell
$env:MEDRESEARCH_DATA_DIR = Join-Path $env:LOCALAPPDATA 'MedResearch'
$case = medresearch case create --title 'Research case A' | ConvertFrom-Json
$doc = medresearch document add $case.id examples/synthetic-report.txt | ConvertFrom-Json

# Review every page against the original. Correct the JSON in a local editor if needed.
$pagesFile = Join-Path $env:MEDRESEARCH_DATA_DIR 'verified-pages.json'
ConvertTo-Json -InputObject @($doc.pages) -Depth 20 | Set-Content -Encoding UTF8 $pagesFile
medresearch document review $case.id $doc.document_id --pages-file $pagesFile

# Prepare only: no PubMed request is sent by this command.
$job = medresearch review $case.id --query 'laboratory biomarkers systematic review' --provider none | ConvertFrom-Json
$job.preview | ConvertTo-Json -Depth 30

# Only after inspecting the exact query and destination:
medresearch research $job.id --approve $job.digest
medresearch status $job.id
medresearch report $job.id --format markdown --out (Join-Path $env:MEDRESEARCH_DATA_DIR 'report.md')
medresearch report $job.id --format json --out (Join-Path $env:MEDRESEARCH_DATA_DIR 'report.json')

```

Use `--provider ollama` for local excerpt matching. Missing/unavailable AI produces
a clearly labeled source-only report. Use `case show CASE_ID` to inspect all
stored page text. Page references are `DOCUMENT_ID:pN`. Preserve page numbering
when saving corrections; set `method` to `manual` for corrected pages. Re-saving
pages invalidates earlier approvals and reports for the case.

Limits: 20 MiB per upload, 100 PDF pages, 30 megapixels per rendered image,
200,000 extracted characters per document/case and 1–50 papers per search.
PDFs must be unencrypted. OCR has a 60-second per-page timeout. Very large cases
may exceed a selected model's context even within these limits. The local adapter
uses the smaller of the model's advertised context and `MEDRESEARCH_OLLAMA_CONTEXT`
(default 32768), reserves output capacity and applies a conservative UTF-8 byte
estimate including template overhead. Oversized input falls back to source-only
output before document text is sent. This estimate is not a tokenizer guarantee.

### Optional cleanup

Deleting a case removes its database documents, jobs, reports, approvals and source cache. Exported files are retained. Run this only when you intend to remove the case:

```powershell
medresearch case delete $case.id
```

### External AI: two separate approvals

Set these environment variables (never put real keys in Git):

```powershell
$env:MEDRESEARCH_EXTERNAL_URL = 'https://your-provider.example/v1'
$env:MEDRESEARCH_EXTERNAL_KEY = 'your-private-key'
$env:MEDRESEARCH_MODEL = 'your-provider-model'
$job = medresearch review CASE_ID --query 'general medical keywords' --provider external | ConvertFrom-Json
$job.preview | ConvertTo-Json -Depth 40
medresearch research $job.id --approve $job.digest
```

After retrieval the same job becomes `awaiting_review` at phase `external_ai`.
No external AI call has occurred yet:

```powershell
$pending = medresearch status $job.id | ConvertFrom-Json
$pending.preview | ConvertTo-Json -Depth 100
# Inspect the ENTIRE body, including masked page text, abstracts, model and URL.
medresearch research $job.id --approve $pending.digest
```

The digest binds the exact stored payload and case revision. It is single-use
and expires after 15 minutes. If expired, edited or incorrect, prepare a new
review. The API key is supplied only as an Authorization header and is never
included in the preview. A compatible provider must support non-streaming
`POST /chat/completions` and JSON response format. Redirects and environment
HTTP proxies are disabled for research/AI clients.
Responses are read incrementally with byte caps and identity encoding; unexpected
compression, redirects and incomplete model responses are rejected. The external
provider must return `finish_reason: "stop"` for a completed response.

Masking removes some labeled names/identifiers, emails, dates and phones.
**It cannot guarantee anonymity** and may alter clinically relevant dates. Review
every outgoing field. If sensitive material remains, correct the local verified
page text or use local AI. Raw documents are not sent to PubMed; only the query
you approve is sent. The approved search allows fetching its returned PMIDs.

## API

The API is local-only and single-process. CLI commands and an API server cannot
open the same workspace concurrently; use the API while the server owns it.

```powershell
$env:MEDRESEARCH_API_TOKEN = medresearch token
medresearch serve --port 8765
```

In another PowerShell window, set the same token and use:

```powershell
$headers = @{ Authorization = "Bearer $env:MEDRESEARCH_API_TOKEN" }
$base = 'http://127.0.0.1:8765'
Invoke-RestMethod "$base/health" -Headers $headers
Invoke-RestMethod "$base/openapi.json" -Headers $headers |
  ConvertTo-Json -Depth 100 | Set-Content -Encoding UTF8 openapi.json
```

All endpoints, including the OpenAPI schema, require the bearer token. Browser
Origin requests are rejected; v1 has no browser UI or third-party CDN docs assets.
Authentication precedes JSON/multipart parsing. At most four requests are buffered
concurrently, with a 30-second body-transfer deadline. Invalid or contradictory
Content-Length headers are rejected. Upload overflow returns 413 even without
Content-Length; a stalled body returns 408.

| Method | Route | Input / result |
| --- | --- | --- |
| POST | `/cases` | JSON `{ "title": "Case A" }` → case ID |
| GET | `/cases/{id}` | Case and all extracted pages |
| POST | `/cases/{id}/documents` | Multipart field `file` → document ID and pages |
| PUT | `/cases/{id}/documents/{doc}` | `{ "pages": [...] }` → verified pages saved |
| POST | `/cases/{id}/reviews` | `{ "query": "...", "provider": "none", "limit": 10 }` → job and preview |
| POST | `/jobs/{id}/research` | `{ "digest": "preview SHA-256" }` → 202, starts background work |
| GET | `/jobs/{id}` | State, phase, current preview/digest, error or report |
| GET | `/jobs/{id}/report?format=json` | Completed report (`markdown` also supported) |
| DELETE | `/cases/{id}` | Remove associated local database records |

Page shape: `{ "number": 1, "text": "verified text", "method": "manual", "warnings": [] }`.
Job states: `awaiting_review` → `queued` → `running` → `completed` or `failed`.
External AI returns to `awaiting_review` between retrieval and analysis. Startup
marks interrupted queued/running jobs failed; it does not resend requests. Prepare
a new review to retry. HTTP 409 describes domain conflicts; HTTP 422 describes
invalid input, HTTP 401 missing/invalid credentials, HTTP 413 oversized requests.

API calls and explicitly requested CLI output contain local private data. Do not
capture them in public logs. Deleting a case does not remove user-created page
JSON files, exported reports, backups or OS temporary files. See [SECURITY.md](../SECURITY.md).
Data directories and report export paths inside any Git checkout are rejected.
Report replacement is atomic; a failed export preserves an existing report.

## Report interpretation

The report separates verbatim document text, retrieved article metadata/abstracts
and model-selected relationships. Every accepted relationship contains an exact
document quote/page reference and exact abstract quote/PMID. Contradictions are
paired document excerpts for review, not confirmed clinical inconsistencies.
No AI-generated free-form clinical claims are published. Empty results mean
insufficient validated output, not absence of disease or conflicting evidence.

PubMed inclusion is not an assessment of evidence quality. The app preserves
publication-type metadata but does not grade studies. Full texts are not downloaded,
and PMC links do not imply that full text was analyzed. Read and appraise original
sources before clinical use. NCBI's [disclaimer and copyright notice](https://www.ncbi.nlm.nih.gov/About/disclaimer.html)
applies. The client follows [NCBI E-utilities guidance](https://www.ncbi.nlm.nih.gov/books/NBK25497/)
with a conservative process-wide request rate, bounded retries and caching.

## Development

```powershell
python -m pip install -c requirements-verified.txt -e ".[dev]"
ruff check .
pytest --cov=medresearch --cov-report=term-missing
python -m build
```

Tests are deterministic and use synthetic medical text, mocked HTTP and mocked
OCR output; they do not transmit real patient data. CI is configured for Windows/Linux/macOS with
Python 3.11/3.12. Live OCR/model smoke tests require separately installed software.
See [VALIDATION.md](../VALIDATION.md) for release verification and remaining limits.

Architecture: `extract` handles page extraction, `storage` SQLite and invalidation,
`service` workflow/approval orchestration, `pubmed` retrieval, `ai` schema and
provenance filtering, `reporting` safe Markdown, and thin `cli`/`api` interfaces.

The 0.1.1 audit, reproduced defects and fixes are documented in [AUDIT.md](../AUDIT.md).

MIT licensed. Contribute only synthetic fixtures and never real patient data.
