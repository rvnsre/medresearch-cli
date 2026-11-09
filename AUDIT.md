# Code audit and fixes — 0.1.1

The audit first added seven regression tests against 0.1.0. All seven failed,
reproducing the issues below; they pass after the fixes. Only synthetic inputs
and controlled HTTP fixtures were used. No real patient data was transmitted.

| Finding | Previous behavior | Fix |
| --- | --- | --- |
| Authentication ran after body parsing | An unauthenticated malformed JSON POST returned 422 after parser work | Authenticate in an outer ASGI boundary before reading any body |
| Streaming upload overflow had the wrong response | A chunked oversized upload produced 400 inside the framework parser | Bound and validate input before routing; consistently return 413 |
| Invalid HTTP length accepted | A negative Content-Length reached the health endpoint successfully | Validate decimal lengths, duplicates, framing conflicts and actual byte count |
| Response limits applied too late | A 20 MB response was fully consumed before a 10 MB size-limit error | Stream responses and stop reading at the cap; close rejected streams |
| Loopback Ollama could route to cloud | A locally named remote alias could receive document text without external approval | Inspect installed-model metadata before sending document text; block remote/cloud aliases |
| Trailing slash configuration broke endpoints | URL validation returned a normalized value that Settings discarded | Store normalized URLs; reject invalid ports/control characters with safe errors |
| Private data protection covered only this checkout | A data directory inside another Git project was accepted | Resolve paths and reject any ancestor with a Git directory/worktree marker |

## Additional fixes

- AI responses must finish normally (`done=true` and `done_reason=stop` locally, `finish_reason=stop`
  externally); malformed/truncated envelopes fall back without publishing partial analysis.
- Local model capacity is checked before inference. An explicit `num_ctx` and a
  conservative input/output budget prevent sending requests likely to truncate.
  The estimate is not a tokenizer guarantee.
- Both PubMed and AI response readers disable redirects even with an injected
  redirect-following client. Unexpected compression is rejected to avoid an
  unbounded decoded body. An identity-encoded response is required.
- PubMed cannot substitute a PMID outside the approved search results.
- Report exports are atomic, use a private temporary file and reject any Git
  checkout. A failed replacement preserves the previous report and removes the
  temporary file. On POSIX the exported file is mode 0600.
- Missing cases are rejected before expensive document extraction, and document
  insertion/deletion rechecks existence within the database write transaction.
- The API caps concurrent buffered requests at four, limits body transfer to
  30 seconds and attaches no-store headers to rejected requests as well as successes.
- Unexpected API/CLI errors expose only a safe message/error class. Their raw
  exception messages and tracebacks are not forwarded to logs or the client.

## Verification and remaining boundaries

The regression suite also exercises redirect credential/body protection, URL
errors, atomic-export failure, model-envelope failures, local context guards,
unexpected compressed responses, slow requests and concurrent buffering limits.
The complete suite, live PubMed/API smoke checks and wheel installation are
recorded in [VALIDATION.md](VALIDATION.md).

This is not a security certification or clinical validation. Local services must
be trusted. Model metadata preflight cannot prevent a locally privileged actor
from changing an alias after inspection; disable cloud in the Ollama server for
sensitive work. Native PDF parsers and Tesseract remain third-party components;
upload/page limits do not provide a full process-level memory sandbox. Do not
expose this single-user service as a public upload endpoint.
