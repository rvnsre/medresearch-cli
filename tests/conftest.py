import httpx
import pytest

from medresearch.config import Settings
from medresearch.models import Page
from medresearch.pubmed import PubMed
from medresearch.service import Service

XML = """<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>12345</PMID><Article>
<ArticleTitle>Synthetic research fixture</ArticleTitle><Journal><JournalIssue><PubDate>
<Year>2025</Year><Month>Jan</Month></PubDate></JournalIssue></Journal>
<Abstract><AbstractText Label="RESULTS">Synthetic study reports a laboratory finding.</AbstractText></Abstract>
<PublicationTypeList><PublicationType>Clinical Trial</PublicationType></PublicationTypeList>
</Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.0000/synthetic</ArticleId>
<ArticleId IdType="pmc">PMC123</ArticleId></ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>"""


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "private", api_token="test-token-" + "x" * 32)


@pytest.fixture
def service(settings):
    result = Service(settings)

    def handler(request):
        if request.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"idlist": ["12345"]}})
        return httpx.Response(200, text=XML)

    result.pubmed = PubMed(result.store, httpx.Client(transport=httpx.MockTransport(handler)))
    return result


@pytest.fixture
def reviewed_case(service):
    case_id = service.store.create("Synthetic case")["id"]
    doc = service.add(case_id, "synthetic.txt", b"Patient: Test Person\nLaboratory finding: synthetic value.")
    service.edit(case_id, doc["document_id"], [Page.model_validate(p) for p in doc["pages"]])
    return case_id, doc["document_id"]
