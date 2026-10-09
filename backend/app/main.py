"""FastAPI service. Run from the backend folder:  uvicorn app.main:app --reload --port 8000"""
import json, os

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from .builders import build_all, build_docx, build_pdf, build_xlsx, build_zip, template_keys
from .engine import analyze, parse_protocol, parse_results
from .narrative import draft_summary

try:  # optional .env support
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = FastAPI(title="Protocol report generator")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
                   allow_methods=["*"], allow_headers=["*"])

MEDIA = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         "pdf": "application/pdf", "zip": "application/zip"}
NAMES = {"docx": "Report.docx", "xlsx": "Results.xlsx", "pdf": "Report_combined.pdf", "zip": "Report package.zip"}


async def _text(f):
    return (await f.read()).decode("utf-8-sig")


async def make_context(protocol, results, references, calibration, template, overrides, use_ai, summary=None):
    """Parse every input, run the deterministic analysis, then draft (or accept) the summary."""
    try:
        p = parse_protocol(protocol.filename or "protocol.json", await _text(protocol))
        rows = parse_results(await _text(results))
        docs = [(f.filename, await _text(f)) for f in references if f.filename]
        pdf = await calibration.read() if calibration and calibration.filename else b""
        tpl = await template.read() if template and template.filename else None
        keys = template_keys(tpl) if tpl else None
        ov = json.loads(overrides or "{}")
    except (ValueError, UnicodeDecodeError) as e:   # JSONDecodeError is a ValueError
        raise HTTPException(422, str(e))
    A = analyze(p, rows, docs, bool(pdf), keys, ov, results.filename or "results.csv")
    text, source = (summary, "user-edited") if summary and summary.strip() else draft_summary(p, A, bool(pdf), use_ai)
    return {"protocol": p, "A": A, "summary": text, "summary_source": source, "has_pdf": bool(pdf), "pdf_bytes": pdf,
            "pdf_name": (calibration.filename if pdf else None), "template": tpl}


@app.get("/api/health")
def health():
    return {"ok": True, "ai_enabled": bool(os.getenv("GROQ_API_KEY"))}


@app.post("/api/analyze")
async def analyze_endpoint(protocol: UploadFile = File(...), results: UploadFile = File(...),
                           references: list[UploadFile] = File([]), calibration: UploadFile | None = File(None),
                           template: UploadFile | None = File(None), overrides: str = Form("{}"), use_ai: bool = Form(False)):
    ctx = await make_context(protocol, results, references, calibration, template, overrides, use_ai)
    return {"status": DRAFT_LABEL, "protocol": ctx["protocol"], **ctx["A"], "summary": ctx["summary"],
            "summary_source": ctx["summary_source"], "attachments": [
                {"label": "Attachment A", "name": ctx["pdf_name"], "status": "included" if ctx["has_pdf"] else "missing"},
                {"label": "Attachment B", "name": "Results.xlsx", "status": "generated"}]}


DRAFT_LABEL = "Draft - Requires Review"


@app.post("/api/export")
async def export_endpoint(protocol: UploadFile = File(...), results: UploadFile = File(...),
                          references: list[UploadFile] = File([]), calibration: UploadFile | None = File(None),
                          template: UploadFile | None = File(None), overrides: str = Form("{}"), use_ai: bool = Form(False),
                          summary: str | None = Form(None), format: str = Form("zip")):
    if format not in MEDIA:
        raise HTTPException(422, "format must be docx, xlsx, pdf or zip")
    ctx = await make_context(protocol, results, references, calibration, template, overrides, use_ai, summary)
    try:
        if format == "xlsx":
            data = build_xlsx(ctx)
        elif format == "docx":
            data = build_docx(ctx)
        elif format == "pdf":
            data = build_pdf(ctx, build_docx(ctx))
        else:
            data = build_zip(build_all(ctx))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return Response(data, media_type=MEDIA[format], headers={"Content-Disposition": f'attachment; filename="{NAMES[format]}"'})