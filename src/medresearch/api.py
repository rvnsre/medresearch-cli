from __future__ import annotations

import secrets
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field

from .config import ResearchError, Settings
from .models import Page, ResearchRequest, ReviewRequest, StrictModel
from .reporting import markdown
from .runtime import workspace


class CaseCreate(StrictModel):
    title: str = Field(min_length=1, max_length=200)


class DocumentReview(StrictModel):
    pages: list[Page] = Field(min_length=1, max_length=100)


def create_app(settings: Settings | None = None, *, service=None):
    settings = settings or Settings()
    if len(settings.api_token) < 24:
        raise ResearchError("Set MEDRESEARCH_API_TOKEN to a random token of at least 24 characters.")

    @asynccontextmanager
    async def lifespan(app):
        if service is not None:
            app.state.service = service
            yield
        else:
            with workspace(settings) as active:
                app.state.service = active
                yield

    bearer = HTTPBearer(auto_error=False)

    def authenticate(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials is None or not secrets.compare_digest(credentials.credentials.encode(), settings.api_token.encode()):
            raise HTTPException(status_code=401, detail="Valid bearer token required.",
                                headers={"WWW-Authenticate": "Bearer"})

    # Disable public schema/docs routes: protected routes below expose them after authentication.
    app = FastAPI(title="MedResearch", version="0.1.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None, dependencies=[Depends(authenticate)])

    @app.exception_handler(ResearchError)
    async def domain_error(request, error):
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # FastAPI normally echoes invalid input; avoid patient text/token leakage.
        sanitized = RequestValidationError([
            {"loc": item["loc"], "msg": "Invalid value", "type": item["type"]} for item in error.errors()
        ])
        return await request_validation_exception_handler(request, sanitized)

    @app.middleware("http")
    async def local_boundary(request, call_next):
        if request.headers.get("host", "").split(":")[0] not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse(status_code=400, content={"detail": "Loopback host required."})
        # No browser clients are part of v1. Reject cross-origin and CSRF-style calls.
        if request.headers.get("origin"):
            return JSONResponse(status_code=403, content={"detail": "Browser-origin requests disabled."})
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length."})
        if length > settings.max_bytes + 1024 * 1024:
            return JSONResponse(status_code=413, content={"detail": "Request too large."})
        # Bound chunked requests too, before multipart parsing allocates disk/memory.
        total = 0
        receive = request._receive

        async def bounded_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > settings.max_bytes + 1024 * 1024:
                raise HTTPException(status_code=413, detail="Request too large.")
            return message

        request._receive = bounded_receive
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/openapi.json", include_in_schema=False)
    def schema():
        return app.openapi()

    @app.post("/cases", status_code=201)
    def create_case(body: CaseCreate):
        return app.state.service.store.create(body.title)

    @app.get("/cases/{case_id}")
    def get_case(case_id: str):
        return {**app.state.service.store.case(case_id),
                "documents": app.state.service.store.documents(case_id)}

    @app.post("/cases/{case_id}/documents", status_code=201)
    def add_document(case_id: str, file: UploadFile = File(...)):
        try:
            content = file.file.read(settings.max_bytes + 1)
            return app.state.service.add(case_id, file.filename or "upload", content)
        finally:
            file.file.close()

    @app.put("/cases/{case_id}/documents/{document_id}")
    def edit_document(case_id: str, document_id: str, body: DocumentReview):
        app.state.service.edit(case_id, document_id, body.pages)
        return {"document_id": document_id, "reviewed": True}

    @app.post("/cases/{case_id}/reviews", status_code=201)
    def prepare(case_id: str, body: ReviewRequest):
        return app.state.service.prepare(case_id, body)

    @app.post("/jobs/{job_id}/research", status_code=202)
    def research(job_id: str, body: ResearchRequest, background: BackgroundTasks):
        job = app.state.service.queue(job_id, body.digest)
        background.add_task(app.state.service.run, job_id)
        return job

    @app.get("/jobs/{job_id}")
    def status(job_id: str):
        return app.state.service.store.job(job_id)

    @app.get("/jobs/{job_id}/report")
    def report(job_id: str, format: str = "json"):
        result = app.state.service.report(job_id)
        if format == "markdown":
            return PlainTextResponse(markdown(result), media_type="text/markdown")
        if format != "json":
            raise HTTPException(status_code=422, detail="Format must be json or markdown.")
        return result

    @app.delete("/cases/{case_id}", status_code=204)
    def delete(case_id: str):
        app.state.service.store.delete(case_id)

    return app
