from __future__ import annotations

import json
import secrets
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import httpx
import pytesseract
import typer
import uvicorn
from pydantic import ValidationError

from .api import create_app
from .config import ResearchError, Settings
from .exporting import write_report
from .models import Page, Provider, ReviewRequest
from .reporting import markdown
from .runtime import workspace

app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False,
                  help="Review-first medical document and PubMed research.")
case_app = typer.Typer(no_args_is_help=True)
document_app = typer.Typer(no_args_is_help=True)
app.add_typer(case_app, name="case")
app.add_typer(document_app, name="document")


@app.callback()
def initialize():
    """Keep redirected output portable across Windows and UTF-8 JSON clients."""
    for stream in (sys.stdout, sys.stderr):
        if not stream.isatty() and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def output(value):
    # Output can contain private documents. It is intentionally printed only on explicit read commands.
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


@contextmanager
def active():
    try:
        with workspace(Settings()) as service:
            yield service
    except (ResearchError, ValidationError, OSError, UnicodeError, json.JSONDecodeError) as error:
        message = str(error) if isinstance(error, ResearchError) else "Invalid input or inaccessible local file."
        typer.echo("Error: " + message, err=True)
        raise typer.Exit(1) from None
    except (typer.Exit, typer.Abort):
        raise
    except Exception as error:
        typer.echo(f"Error: Operation failed ({type(error).__name__}).", err=True)
        raise typer.Exit(1) from None


@app.command()
def doctor():
    """Check local OCR, language packs, Ollama and configuration; no patient data is sent."""
    try:
        settings = Settings()
    except ResearchError as error:
        typer.echo("Error: " + str(error), err=True)
        raise typer.Exit(1) from None
    result = {"data_directory": str(settings.data_dir), "configured_model": settings.model or None,
              "api_token_configured": len(settings.api_token) >= 24,
              "external_configured": bool(settings.external_url and settings.external_key and settings.model)}
    result["local_ai_notice"] = "Ollama models are preflight-checked. Disable cloud on the Ollama server for sensitive data."
    result["ollama_context_cap"] = settings.ollama_context
    pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
    try:
        result["ocr_languages"] = pytesseract.get_languages(config="")
        result["missing_ocr_languages"] = sorted(set(settings.ocr_languages.split("+")) - set(result["ocr_languages"]))
    except (OSError, RuntimeError, pytesseract.TesseractNotFoundError):
        result["ocr_status"] = "Unavailable; install Tesseract and aze/rus/eng language packs."
    try:
        with httpx.Client(timeout=3, trust_env=False, follow_redirects=False) as client:
            response = client.get(settings.ollama_url + "/api/tags")
            response.raise_for_status()
            result["ollama_models"] = [m["name"] for m in response.json()["models"]]
            result["configured_model_installed"] = settings.model in result["ollama_models"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        result["ollama_status"] = "Unavailable; document extraction and PubMed research with --provider none still work."
    output(result)


@app.command("token")
def token():
    """Generate a local API token; set it in MEDRESEARCH_API_TOKEN yourself."""
    typer.echo(secrets.token_urlsafe(32))


@case_app.command("create")
def create_case(title: Annotated[str, typer.Option(help="Use a non-identifying case title.")] = "Untitled case"):
    if not 1 <= len(title) <= 200:
        raise typer.BadParameter("Title must be 1–200 characters.")
    with active() as service:
        output(service.store.create(title))


@case_app.command("show")
def show_case(case_id: str):
    with active() as service:
        output({**service.store.case(case_id), "documents": service.store.documents(case_id)})


@case_app.command("delete")
def delete_case(case_id: str, yes: Annotated[bool, typer.Option("--yes", help="Confirm deletion.")] = False):
    if not yes:
        typer.confirm("Delete this case, page text, cached sources, approvals and reports?", abort=True)
    with active() as service:
        service.store.delete(case_id)
        typer.echo("Case and associated local database records deleted. Exported files are not removed.")


@document_app.command("add")
def add_document(case_id: str, path: Path):
    with active() as service:
        with path.open("rb") as source:
            content = source.read(service.settings.max_bytes + 1)
        output(service.add(case_id, path.name, content))


@document_app.command("review")
def review_document(case_id: str, document_id: str,
                    pages_file: Annotated[Path, typer.Option(help="JSON array of verified pages; preserve numbering.")]):
    """Save human-verified pages. Export the current pages with case show first."""
    with active() as service:
        raw = json.loads(pages_file.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, list):
            raise ResearchError("Pages file must contain a JSON array.")
        service.edit(case_id, document_id, [Page.model_validate(p) for p in raw])
        output({"document_id": document_id, "reviewed": True})


@app.command()
def review(case_id: str, query: Annotated[str, typer.Option()],
           provider: Annotated[Provider, typer.Option()] = "ollama",
           limit: Annotated[int, typer.Option(min=1, max=50)] = 10):
    """Prepare and display the exact outbound search preview without sending it."""
    with active() as service:
        output(service.prepare(case_id, ReviewRequest(query=query, provider=provider, limit=limit)))


@app.command()
def research(job_id: str, approve: Annotated[str, typer.Option(help="Digest of the exact preview you reviewed.")]):
    """Approve a single payload and run it. External AI needs a second approval."""
    with active() as service:
        service.queue(job_id, approve)
        job = service.run(job_id)
        output(job)
        if job["state"] == "failed":
            raise typer.Exit(1)


@app.command()
def status(job_id: str):
    with active() as service:
        output(service.store.job(job_id))


@app.command()
def report(job_id: str, format: Annotated[str, typer.Option()] = "markdown",
           out: Annotated[Path | None, typer.Option(help="Private export path outside the source checkout.")] = None):
    if format not in {"markdown", "json"}:
        raise typer.BadParameter("Format must be markdown or json.")
    with active() as service:
        result = service.report(job_id)
        text = markdown(result) if format == "markdown" else json.dumps(result, ensure_ascii=False, indent=2)
        if out:
            write_report(out, text)
            typer.echo("Report exported.")
        else:
            typer.echo(text)


@app.command()
def serve(port: Annotated[int, typer.Option(min=1024, max=65535)] = 8765):
    """Run a single local API process. Set MEDRESEARCH_API_TOKEN first."""
    try:
        api = create_app()
    except ResearchError as error:
        typer.echo("Error: " + str(error), err=True)
        raise typer.Exit(1) from None
    uvicorn.run(api, host="127.0.0.1", port=port, access_log=False, log_level="warning")


if __name__ == "__main__":
    app()
