from __future__ import annotations

import hashlib
import json
import threading
import time

import httpx
from defusedxml import ElementTree as ET

from .config import ResearchError
from .models import Article
from .storage import Store, encode

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_rate_lock = threading.Lock()
_last_request = 0.0


def node_text(node) -> str:
    return "" if node is None else "".join(node.itertext()).strip()


def parse_articles(xml: str) -> list[Article]:
    try:
        root = ET.fromstring(xml)
        if root.tag != "PubmedArticleSet" or root.find("ERROR") is not None:
            raise ValueError("Unexpected PubMed response")
        articles, seen_pmids, seen_dois = [], set(), set()
        for entry in root.findall(".//PubmedArticle"):
            pmid = node_text(entry.find(".//MedlineCitation/PMID"))
            if not pmid.isdigit():
                continue
            doi = next((node_text(n).lower().removeprefix("https://doi.org/").removeprefix("http://doi.org/")
                        for n in entry.findall(".//ArticleId")
                        if n.get("IdType") == "doi"), None)
            if pmid in seen_pmids or (doi and doi in seen_dois):
                continue
            seen_pmids.add(pmid)
            if doi:
                seen_dois.add(doi)
            abstract = "\n".join((f"{n.get('Label')}: " if n.get("Label") else "") + node_text(n)
                                 for n in entry.findall(".//Abstract/AbstractText"))
            date = entry.find(".//JournalIssue/PubDate")
            date_text = " ".join(node_text(n) for n in date) if date is not None else "Unknown"
            pmc = any(n.get("IdType") == "pmc" for n in entry.findall(".//ArticleId"))
            articles.append(Article(
                pmid=pmid, doi=doi, title=node_text(entry.find(".//ArticleTitle")), date=date_text,
                publication_types=[node_text(n) for n in entry.findall(".//PublicationType")],
                abstract=abstract, availability=("PMC link available; full text not retrieved" if pmc else
                                                "Full-text availability not established"),
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            ))
        return articles
    except Exception:
        raise ResearchError("PubMed returned invalid article data.") from None


class PubMed:
    def __init__(self, store: Store, client: httpx.Client | None = None):
        self.store = store
        self.client = client

    def _get(self, path, params):
        global _last_request
        for attempt in range(3):
            with _rate_lock:
                delay = 0.36 - (time.monotonic() - _last_request)
                if delay > 0:
                    time.sleep(delay)
                _last_request = time.monotonic()
            try:
                if self.client:
                    response = self.client.get(f"{BASE}/{path}", params=params)
                else:
                    with httpx.Client(timeout=25, follow_redirects=False, trust_env=False) as client:
                        response = client.get(f"{BASE}/{path}", params=params)
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        time.sleep(0.5 * 2**attempt)
                        continue
                response.raise_for_status()
                if len(response.content) > 10_000_000:
                    raise ResearchError("PubMed response exceeded size limit.")
                return response
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt < 2:
                    time.sleep(0.5 * 2**attempt)
                    continue
            except httpx.HTTPStatusError:
                break
        raise ResearchError("PubMed unavailable after bounded retries; try again later.")

    def search(self, case_id: str, preview: dict) -> list[Article]:
        key = hashlib.sha256(encode(preview["search_params"]).encode()).hexdigest()
        with self.store.connect() as db:
            row = db.execute("SELECT value FROM cache WHERE case_id=? AND key=? AND expires>?",
                             (case_id, key, time.time())).fetchone()
        if row:
            return [Article.model_validate(a) for a in json.loads(row[0])]
        try:
            response = self._get("esearch.fcgi", preview["search_params"]).json()
            result = response["esearchresult"]
            if result.get("errorlist") or response.get("error"):
                raise ResearchError("PubMed rejected the search terms. Revise the query.")
            ids = result["idlist"]
            if not isinstance(ids, list) or not all(isinstance(i, str) and i.isdigit() for i in ids):
                raise ValueError
            # Server-provided IDs cannot become arbitrary URLs or new search terms.
            ids = ids[:preview["search_params"]["retmax"]]
            articles = [] if not ids else parse_articles(self._get("efetch.fcgi", {
                "db": "pubmed", "id": ",".join(ids), "retmode": "xml", "tool": "medresearch-cli",
            }).text)
        except ResearchError:
            raise
        except (ValueError, KeyError, TypeError):
            raise ResearchError("PubMed returned invalid search data.") from None
        with self.store.connect() as db:
            db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?)",
                       (case_id, key, encode([a.model_dump() for a in articles]), time.time() + 86400))
        return articles
