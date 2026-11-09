# Security and private data

MedResearch is a single-user local research tool, not a hosted clinical system.
Bind only to loopback. Do not expose it through a tunnel or reverse proxy. Use a
random bearer token and a private data directory on an encrypted disk. On Windows,
restrict the directory ACL to your account; POSIX permissions alone do not set Windows ACLs.

Masking is heuristic, not certified anonymization. It misses unlabeled names,
identifiers, dates and context. Review the exact outgoing query and, separately,
the complete external AI JSON payload before approving its SHA-256 digest.
Approval expires after 15 minutes, is single-use, and is revoked by document edits.
Digest approval means the application transmits exactly the reviewed payload;
it does not mean the payload is safe or the research is clinically correct.

Original uploads are processed then discarded; extracted text is kept in SQLite.
Multipart uploads may be temporarily spooled by the framework/OS before parsing.
Reports, preview payloads, reviewed page text and search cache can contain private
data. Do not commit them. The default data directory is outside the checkout.
API responses disable caching; access logs are disabled by the CLI server.
Do not put identifiers in URLs, case titles, filenames, command-line arguments or queries.
Shell history and redirected CLI output are outside this application's control.

Case deletion cascades through documents, jobs, reports and cached articles.
SQLite secure_delete is enabled. Deletion is not forensic erasure from storage,
backups, OS temporary files, shell history or user-exported reports. Exported files
remain under user control.

AI has no tool execution capability. Its output is schema-constrained and only
exact source quotes with resolvable page references/PMIDs are accepted. This
reduces injection impact and fabricated citations, but does not establish semantic
correctness. A model can still select irrelevant, misleading or incomplete quotes.
All findings require professional appraisal. No clinical validation is claimed.

Use synthetic data when reporting bugs. Do not open public issues containing
patient data, tokens, screenshots of real documents or private API payloads.
