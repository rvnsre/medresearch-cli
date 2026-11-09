from __future__ import annotations

import html
import re


def safe(text) -> str:
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "", str(text))
    text = html.escape(text, quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", text).replace("\n", " ")


def markdown(report: dict) -> str:
    lines = ["# Medical document research report", "", f"Generated: {report['generated_at']}", "",
             "Research support for professional review. Not a diagnosis, prescription or treatment decision.",
             "", "## Document summary and source facts", ""]
    for doc in report["documents"]:
        lines += [f"### {safe(doc['name'])}", ""]
        for page in doc["pages"]:
            lines += [f"**{safe(doc['id'])}:p{page['number']}** ({page['method']}; reviewed)", "",
                      "> " + safe(page["text"] or "[No readable text]"), ""]
            lines += ["- " + safe(w) for w in page["warnings"]]
            lines.append("")
    lines += ["## AI-selected findings — paired excerpts for review", "",
              "These are model-selected excerpt pairs, not established clinical conclusions.", ""]
    for f in report["findings"]:
        lines += [f"- Document {safe(f['doc_ref'])}: “{safe(f['document_quote'])}”",
                  f"  Abstract [PMID {f['pmid']}](https://pubmed.ncbi.nlm.nih.gov/{f['pmid']}/): "
                  f"“{safe(f['evidence_quote'])}”", ""]
    if not report["findings"]:
        lines += ["No validated excerpt pairs available.", ""]
    lines += ["## Potential document contradictions", ""]
    for c in report["conflicts"]:
        lines += [f"- {safe(c['left_ref'])}: “{safe(c['left_quote'])}” versus "
                  f"{safe(c['right_ref'])}: “{safe(c['right_quote'])}”", ""]
    if not report["conflicts"]:
        lines += ["None identified by validated AI output; contradictions may still exist.", ""]
    lines += ["## Evidence table — abstract-level retrieval", "",
              "| PMID | Title | Publication date | Study types | Availability |",
              "| --- | --- | --- | --- | --- |"]
    for a in report["articles"]:
        lines.append(f"| [{a['pmid']}]({a['url']}) | {safe(a['title'])} | {safe(a['date'])} | "
                     f"{safe(', '.join(a['publication_types']))} | {safe(a['availability'])} |")
    lines += ["", "### Retrieved abstracts", ""]
    for a in report["articles"]:
        lines += [f"**PMID {a['pmid']}**", "", "> " + safe(a["abstract"] or "No abstract provided."), ""]
    lines += ["## Limitations", ""] + ["- " + safe(x) for x in report["limitations"]]
    lines += ["", "## Questions for professional review", ""]
    lines += ["- " + safe(x) for x in report["questions"]]
    lines += ["", "NCBI notice: https://www.ncbi.nlm.nih.gov/About/disclaimer.html", ""]
    return "\n".join(lines)
