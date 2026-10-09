# Protocol Report Generator (mini prototype)

Takes a protocol, raw CSV test results, reference documents and a supporting PDF, and produces a **draft report package**: an editable DOCX, an Excel file, a combined PDF and a ZIP. All data is synthetic. Every output is marked **Draft - Requires Review**.

**Stack:** React + Vite (frontend), FastAPI with python-docx, openpyxl, ReportLab and pypdf (backend), optional Groq for the summary.

## Setup

Requirements: Python 3.10+, Node 18+.

```bash
# 1. Backend (terminal 1)
cd backend
pip install -r requirements.txt
cp .env.example .env            # optional: add GROQ_API_KEY for the AI summary
uvicorn app.main:app --reload --port 8000

# 2. Frontend (terminal 2, project root)
npm install
npm run dev                     # http://localhost:5173 (Vite proxies /api to port 8000)

# 3. Smoke test of the three demo cases (needs test-inputs/ next to backend/)
cd backend && python tests/test_cases.py
```

Without `GROQ_API_KEY` everything works; the summary is then written by deterministic rules.

## How to use

1. Upload a protocol, a results CSV, reference `.md` files and (optionally) a calibration PDF, or click **Load sample files**.
2. Click **Generate report**. Review the preview, the validation checklist and the attachment index.
3. Edit any measurement or the summary. Pass/Fail, checks and counts update immediately.
4. Export the DOCX, XLSX, combined PDF or the ZIP package.

Sample inputs are in `test-inputs/`. Generated outputs for each demo case are in `sample-outputs/`.

## Project structure

```
backend/
  app/main.py         FastAPI routes
  app/engine.py       parsing, retrieval, Pass/Fail, validation checks, rule-based summary
  app/narrative.py    optional Groq summary + grounding checks
  app/builders.py     DOCX, XLSX, PDF, ZIP generation
  tests/test_cases.py smoke test of the three demo cases
src/
  App.jsx             UI, live preview, upload slots, export
  lib/engine.js       browser copy of the engine (instant preview only)
  api.js              calls the backend
test-inputs/          shared inputs + one results.csv per demo case
sample-outputs/       generated DOCX, XLSX, PDF, validation notes per case
```

## Architecture

```
React UI  --/api-->  FastAPI (main.py)
 live preview          engine.py     parse, retrieve, Pass/Fail, checks
 (lib/engine.js)       narrative.py  optional Groq summary + checks
                       builders.py   DOCX, XLSX, PDF, ZIP
```

`engine.py` is the source of truth for every exported file. The JavaScript engine is a port used only so the preview updates instantly while editing; exports recompute everything on the server from the raw inputs plus the user's edits. Every output is built from one `analyze()` result, so the report, narrative and Excel always agree.

## Report template

The report has four sections. Fixed wording (for example "This report was prepared in accordance with the approved protocol.") is protected: only paragraphs that contain `{{placeholders}}` are changed. Placeholders: `project_id`, `component`, `test_method`, `acceptance_range`, `calibration_ref`, `retrieved_guidance`, `results_table`, `summary`. A placeholder that cannot be filled (for example `calibration_ref` when no PDF is supplied) stays visible, highlighted in yellow, and is listed in the validation checklist. A sample template is at `test-inputs/shared/report-template.docx`.

## How each section is generated

| Section | Approach | Content comes from |
|---|---|---|
| 1. Project information and fixed statements | Deterministic, template-based | Protocol fields fill the placeholders; protected wording is never edited |
| 2. Test method and guidance | RAG (retrieval) | Reference `.md` files are split into paragraph chunks with section refs; a local IDF-weighted keyword index returns the top passages, each shown as `[file, §ref]` |
| 3. Results and evidence | Deterministic | CSV rows judged against the protocol range (`min <= value <= max`, inclusive); each row records its source (`results.csv, row N`) |
| 4. Summary and observations | AI-assisted | Written from verified results plus retrieved passages (Groq if configured, otherwise rules). The user reviews and edits it before export |

**AI never calculates results or acceptance criteria.** A Groq draft is rejected, and the rule-based summary used instead, if it cites a source that was not retrieved, omits a failing or missing sample, contains an unverified number, or uses causal wording.

## Output files

- **Report.docx**: editable report with results table, source citations, attachment index and a "Draft - Requires Review" header.
- **Attachment B - Results.xlsx**: sample IDs, measurements, units, observations, limits, calculated Pass/Fail, source rows, and totals (total, passed, failed).
- **Report_combined.pdf**: the report, then the supplied PDF stamped "Attachment A" (or a "not supplied" page), then the results table as "Attachment B".
- **Report package.zip**: DOCX, combined PDF, XLSX and the original supporting PDF (omitted if none was supplied).

## API

| Route | Purpose |
|---|---|
| `GET /api/health` | Service status and whether a Groq key is set |
| `POST /api/analyze` | Multipart: `protocol`, `results`, `references[]`, `calibration?`, `template?`, `overrides?`, `use_ai?`. Returns results, checks, passages, summary and attachments |
| `POST /api/export` | Same inputs plus `summary` and `format` (`docx`, `xlsx`, `pdf` or `zip`). Returns the file |

## Validation checklist

Shown in the UI and in `validation.txt` of each sample output: sample count, sample IDs (missing, unplanned, duplicate), missing measurements, units, required attachments, acceptance range, unfilled template placeholders and reference documents loaded. An out-of-range result stays red and is reported as Fail in every output; AI cannot rewrite it.

## The three demo cases

| Case | Inputs | Result |
|---|---|---|
| Complete | shared files + `case-1-complete/results.csv` | 5 of 5 pass, no warnings |
| Failing | `case-2-failing/results.csv` (S-003 = 2.71 mm, limit 2.60) | 1 Fail, identical in the report, summary, Excel and PDF |
| Incomplete | `case-3-incomplete/results.csv` (S-004 removed), no calibration PDF | Warnings for the missing sample and missing PDF; `{{calibration_ref}}` stays highlighted; nothing fabricated |

## Known limitations

- Retrieval is keyword-based (IDF), not embedding-based, so it matches words rather than meaning.
- The Groq call was tested with mocked responses only. The model name may need changing for your account.
- The grounding check verifies decimal numbers but not integer counts in AI text.
- The preview engine (JavaScript) and export engine (Python) are two copies of the same logic and can drift.
- The XLSX stores calculated values, not live Excel formulas.
- The PDF is rendered from the DOCX text and tables with ReportLab; it is readable but does not reproduce Word's exact layout.
- Filling a placeholder replaces that paragraph's run formatting with plain text.
- The UI has no upload slot for a custom report template (the API accepts one as the `template` field).
- Placeholders inside headers, footers or table cells of an uploaded template are not detected.
- No authentication, database or persistence; files are processed in memory per request. OCR and scanned references are out of scope.

## AI tools and assistance

See `AI_USAGE.md`.