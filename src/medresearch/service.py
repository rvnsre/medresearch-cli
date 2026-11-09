from __future__ import annotations

import json
import secrets
import time
from datetime import UTC, datetime
from uuid import uuid4

from .ai import AI, request_payload, verified_analysis
from .config import ResearchError, Settings
from .extract import extract
from .models import Analysis, Article, Page, ReviewRequest
from .privacy import digest, mask
from .pubmed import BASE, PubMed
from .storage import Store, encode


class Service:
    def __init__(self, settings: Settings, *, pubmed=None, ai=None):
        self.settings = settings
        self.store = Store(settings.data_dir)
        self.pubmed = pubmed or PubMed(self.store)
        self.ai = ai or AI(settings)

    def add(self, case_id: str, name: str, content: bytes):
        pages = extract(name, content, self.settings)
        # Original uploads are never persisted; only page text and warnings are stored.
        name = name.replace("\\", "/").rsplit("/", 1)[-1][:200]
        doc_id = self.store.add_document(case_id, name, [p.model_dump() for p in pages])
        return {"document_id": doc_id, "pages": [p.model_dump() for p in pages], "reviewed": False}

    def edit(self, case_id, doc_id, pages: list[Page]):
        if sum(len(p.text) for p in pages) > self.settings.max_text_chars:
            raise ResearchError("Document text exceeds 200,000 characters.")
        if len(pages) > self.settings.max_pages:
            raise ResearchError("Too many pages.")
        self.store.edit_document(case_id, doc_id, [p.model_dump() for p in pages])

    def prepare(self, case_id: str, request: ReviewRequest):
        case = self.store.case(case_id)
        docs = self.store.documents(case_id)
        if not docs or not all(d["reviewed"] for d in docs):
            raise ResearchError("Review and save every document's extracted pages before research.")
        if not any(p["text"].strip() for d in docs for p in d["pages"]):
            raise ResearchError("At least one reviewed page must contain readable text.")
        if sum(len(p["text"]) for d in docs for p in d["pages"]) > self.settings.max_text_chars:
            raise ResearchError("Case text exceeds 200,000 characters; split the case.")
        if request.provider == "external" and not (self.settings.external_url and self.settings.model
                                                    and self.settings.external_key):
            raise ResearchError("Configure external URL, model and API key, or select none/ollama.")
        job_id = uuid4().hex
        preview = {
            "phase": "search", "case_id": case_id, "job_id": job_id,
            "revision": case["revision"], "destination": BASE,
            "search_params": {"db": "pubmed", "term": mask(request.query), "retmode": "json",
                              "retmax": request.limit, "sort": "relevance", "tool": "medresearch-cli"},
            "followup": {"endpoint": BASE + "/efetch.fcgi", "db": "pubmed", "retmode": "xml",
                         "tool": "medresearch-cli", "ids": "Only PMIDs returned by this approved search"},
            "provider": request.provider,
            "notice": "Review the exact search parameters. Masking is incomplete. No document text is sent "
                      "to PubMed. External AI requires a separate, exact-payload approval after retrieval.",
        }
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT revision FROM cases WHERE id=?", (case_id,)).fetchone()
            if current is None or current[0] != case["revision"]:
                raise ResearchError("Case changed while preparing review; retry.")
            if db.execute("SELECT 1 FROM jobs WHERE case_id=? AND state IN ('queued','running')", (case_id,)).fetchone():
                raise ResearchError("Wait for the active job.")
            db.execute("UPDATE jobs SET state='failed',error='Superseded by a new review.' "
                       "WHERE case_id=? AND state='awaiting_review'", (case_id,))
            db.execute("INSERT INTO jobs(id,case_id,state,phase,revision,preview,digest,expires,provider) "
                       "VALUES (?,?,?,?,?,?,?,?,?)", (job_id, case_id, "awaiting_review", "search",
                                                      case["revision"], encode(preview), digest(preview),
                                                      time.time() + self.settings.approval_ttl, request.provider))
        return self.store.job(job_id)

    def queue(self, job_id: str, approved_digest: str):
        if len(approved_digest) != 64 or any(c not in "0123456789abcdef" for c in approved_digest):
            raise ResearchError("Approval must be a SHA-256 preview digest.")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT jobs.*, cases.revision AS current_revision FROM jobs JOIN cases "
                             "ON cases.id=jobs.case_id WHERE jobs.id=?", (job_id,)).fetchone()
            if row is None:
                raise ResearchError("Job not found.")
            if row["state"] != "awaiting_review":
                raise ResearchError("This job is not awaiting approval; approval is single-use.")
            if row["revision"] != row["current_revision"] or row["expires"] < time.time():
                raise ResearchError("Approval expired or case changed; prepare a new review.")
            if not secrets.compare_digest(row["digest"], approved_digest):
                raise ResearchError("Approval does not match the exact preview digest.")
            if digest(json.loads(row["preview"])) != row["digest"]:
                raise ResearchError("Preview integrity check failed.")
            db.execute("INSERT INTO approvals(job_id,phase,digest,preview) VALUES (?,?,?,?)",
                       (job_id, row["phase"], row["digest"], row["preview"]))
            db.execute("UPDATE jobs SET state='queued' WHERE id=?", (job_id,))
        return self.store.job(job_id)

    def run(self, job_id: str):
        with self.store.connect() as db:
            changed = db.execute("UPDATE jobs SET state='running' WHERE id=? AND state='queued'", (job_id,)).rowcount
        if not changed:
            raise ResearchError("Job must be queued exactly once before execution.")
        job = self.store.job(job_id)
        try:
            docs = self.store.documents(job["case_id"])
            pages = {f"{d['id']}:p{p['number']}": p["text"] for d in docs for p in d["pages"]}
            provider = job["provider"]
            notes, analysis, analyzed = [], Analysis(), False
            if job["phase"] == "search":
                articles = self.pubmed.search(job["case_id"], job["preview"])
                if provider == "external" and articles:
                    masked_pages = {ref: mask(text) for ref, text in pages.items()}
                    preview = {"phase": "external_ai", "case_id": job["case_id"], "job_id": job_id,
                               "revision": job["revision"],
                               **request_payload(self.settings, provider, masked_pages, articles),
                               "notice": "Exact outgoing JSON body. Authorization uses your configured key "
                                         "(not displayed). Masking is incomplete; review every excerpt. "
                                         "Nothing is sent until this digest is approved."}
                    with self.store.connect() as db:
                        db.execute("UPDATE jobs SET state='awaiting_review',phase='external_ai',preview=?,"
                                   "digest=?,expires=?,articles=? WHERE id=?", (
                                       encode(preview), digest(preview), time.time() + self.settings.approval_ttl,
                                       encode([a.model_dump() for a in articles]), job_id))
                    return self.store.job(job_id)
                if provider == "ollama" and articles and self.settings.model:
                    try:
                        analysis = self.ai.analyze(provider, request_payload(self.settings, provider, pages, articles))
                        analyzed = True
                    except ResearchError as error:
                        notes.append(str(error))
                elif provider != "none":
                    notes.append("AI analysis not performed: no configured model or no articles returned.")
            else:
                articles = [Article.model_validate(a) for a in job["articles"]]
                # Approval is for the stored destination and exact body, never regenerated model output.
                if job["preview"]["url"] != self.settings.external_url + "/chat/completions":
                    raise ResearchError("External destination changed; prepare a new review.")
                try:
                    analysis = self.ai.analyze("external", job["preview"])
                    analyzed = True
                except ResearchError as error:
                    notes.append(str(error))
                # External AI quotes refer to the masked page text that was actually transmitted.
                input_data = json.loads(job["preview"]["body"]["messages"][1]["content"])
                pages = input_data["documents"]
            findings, conflicts, dropped = verified_analysis(analysis, pages, articles)
            if dropped:
                notes.append(f"Discarded {dropped} AI items with unresolved references or non-verbatim excerpts.")
            if not analyzed:
                notes.append("AI analysis was not performed successfully. Findings and contradictions were not assessed.")
            report = {
                "schema_version": "1.0", "case_id": job["case_id"], "job_id": job_id,
                "generated_at": datetime.now(UTC).isoformat(), "language": "en",
                "query": self.store.approvals(job_id)[0]["preview"]["search_params"]["term"],
                "ai_performed": analyzed, "ai_provider": provider,
                "approved_requests": self.store.approvals(job_id),
                "documents": docs, "articles": [a.model_dump() for a in articles],
                "findings": findings, "conflicts": conflicts,
                "limitations": notes + [
                    "Abstract-level retrieval only; full texts and guideline completeness were not assessed.",
                    "Exact quote validation checks provenance, not clinical accuracy or applicability.",
                    "OCR, extraction, masking and AI matching may miss or misinterpret information.",
                    "No automated diagnosis, prescription or treatment recommendation is generated.",
                    "This software has not been clinically validated.",
                ],
                "questions": [
                    "Do extracted dates, units, numbers and negations match the original documents?",
                    "Are apparent contradictions explained by timing, specimen type or clinical context?",
                    "Do study populations and endpoints apply to this case, after full-text appraisal?",
                    "What relevant guidelines, evidence or documents are missing from this search?",
                ],
            }
            with self.store.connect() as db:
                db.execute("UPDATE jobs SET state='completed',report=?,articles=?,error=NULL WHERE id=?",
                           (encode(report), encode(report["articles"]), job_id))
        except Exception as error:
            # Unexpected library errors can embed request bodies; never persist their text.
            message = str(error) if isinstance(error, ResearchError) else "Research failed; no report generated."
            with self.store.connect() as db:
                db.execute("UPDATE jobs SET state='failed',error=? WHERE id=?", (message, job_id))
        return self.store.job(job_id)

    def report(self, job_id):
        job = self.store.job(job_id)
        if job["state"] != "completed" or job["report"] is None:
            raise ResearchError("No current completed report is available.")
        return job["report"]
