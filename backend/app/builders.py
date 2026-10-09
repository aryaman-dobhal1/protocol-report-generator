"""Output files: DOCX (filled from the template), XLSX, combined PDF, ZIP."""
import copy, io, re, zipfile
from xml.sax.saxutils import escape as esc

from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from docx.shared import Pt, RGBColor
from docx.table import Table as DTable
from docx.text.paragraph import Paragraph as DPar
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .engine import DEFAULT_KEYS, FIXED, PLACEHOLDER, fmt, placeholder_values

DRAFT = "Draft - Requires Review"
AMBER = RGBColor(0xB2, 0x50, 0x00)


# ---------- DOCX ----------
def template_keys(data):
    try:
        doc = Document(io.BytesIO(data))
    except Exception:
        raise ValueError("The report template is not a valid .docx file.")
    text = "\n".join(p.text for p in doc.paragraphs)
    return list(dict.fromkeys(PLACEHOLDER.findall(text)))


def default_template(fixed):
    d = Document()
    d.add_heading("Component Inspection Report", 0)
    d.add_heading("1. Project information", 2)
    for label, key in [("Project ID", "project_id"), ("Component", "component"), ("Test method", "test_method"),
                       ("Acceptance range", "acceptance_range"), ("Calibration reference", "calibration_ref")]:
        d.add_paragraph(f"{label}: {{{{{key}}}}}")
    d.add_paragraph(fixed).runs[0].italic = True
    d.add_heading("2. Test method and guidance", 2)
    d.add_paragraph("{{retrieved_guidance}}")
    d.add_heading("3. Results and evidence", 2)
    d.add_paragraph("{{results_table}}")
    d.add_paragraph("Full results table: Attachment B. Calibration record: Attachment A.")
    d.add_heading("4. Summary and observations", 2)
    d.add_paragraph("{{summary}}")
    return d


def _grid(table):
    """'Table Grid' may not exist in a user's template, so fall back to explicit borders."""
    try:
        table.style = "Table Grid"
    except KeyError:
        from docx.oxml import parse_xml
        from docx.oxml.ns import nsdecls
        edges = "".join(f'<w:{e} w:val="single" w:sz="4" w:space="0" w:color="999999"/>' for e in ("top", "left", "bottom", "right", "insideH", "insideV"))
        table._tbl.tblPr.append(parse_xml(f"<w:tblBorders {nsdecls('w')}>{edges}</w:tblBorders>"))


def _heading(doc, text):
    try:
        return doc.add_heading(text, 2)
    except KeyError:   # template without heading styles
        run = doc.add_paragraph().add_run(text)
        run.bold, run.font.size = True, Pt(14)


def _insert_after(par, text):
    el = copy.deepcopy(par._p)
    par._p.addnext(el)
    new = DPar(el, par._parent)
    new.text = text
    return new


def _results_table(doc, par, A, unit):
    rows = [(r["id"], fmt(r["value"]), r["unit"], r["observation"], r["label"], r["status"]) for r in A["rows"]]
    rows += [(m, "-", "-", "No result supplied", "Missing", "missing") for m in A["missing"]]
    t = doc.add_table(rows=1, cols=5)
    _grid(t)
    for c, h in zip(t.rows[0].cells, ["Sample", f"Measurement ({unit})", "Unit", "Observation", "Result"]):
        c.text = h
        c.paragraphs[0].runs[0].bold = True
    for *vals, st in rows:
        cells = t.add_row().cells
        for c, v in zip(cells, vals):
            c.text = str(v)
        if st != "pass":
            run = cells[4].paragraphs[0].runs[0]
            run.bold = True
            run.font.color.rgb = RGBColor(0xC4, 0x16, 0x1C) if st == "fail" else AMBER
    par._p.addnext(t._tbl)
    par._p.getparent().remove(par._p)


def build_docx(ctx):
    p, A = ctx["protocol"], ctx["A"]
    doc = Document(io.BytesIO(ctx["template"])) if ctx["template"] else default_template(p["fixed_statement"])
    vals = placeholder_values(p, A["range"], A["passages"], ctx["has_pdf"], ctx["summary"])
    for par in list(doc.paragraphs):           # protected wording is untouched: only paragraphs with {{...}} change
        text, keys = par.text, PLACEHOLDER.findall(par.text)
        if not keys:
            continue
        if "results_table" in keys:
            _results_table(doc, par, A, p["unit"])
            continue
        if "retrieved_guidance" in keys and vals["retrieved_guidance"]:
            lines = vals["retrieved_guidance"].split("\n")
            par.text = text.replace("{{retrieved_guidance}}", lines[0])
            for ln in lines[1:]:
                par = _insert_after(par, ln)
            continue
        new = text
        for k in set(keys):
            if vals.get(k) is not None:
                new = new.replace("{{%s}}" % k, str(vals[k]))
        if new != text:
            par.text = new
        if PLACEHOLDER.search(par.text):       # unfilled: stays visible and highlighted, never guessed
            for r in par.runs:
                r.font.highlight_color = WD_COLOR_INDEX.YELLOW
    _heading(doc, "Attachment index")
    t = doc.add_table(rows=1, cols=3)
    _grid(t)
    for c, h in zip(t.rows[0].cells, ["Label", "Document", "Status"]):
        c.text = h
        c.paragraphs[0].runs[0].bold = True
    for row in [("Attachment A", "Calibration record (supporting PDF)", "Included" if ctx["has_pdf"] else "Not supplied"),
                ("Attachment B", "Results table (Results.xlsx)", "Included")]:
        for c, v in zip(t.add_row().cells, row):
            c.text = v
    doc.add_paragraph("Pass/Fail is calculated from results.csv against the protocol acceptance range. The summary is a draft and requires review.")
    for s in doc.sections:                      # draft marking on every page
        hp = s.header.paragraphs[0]
        hp.text = DRAFT
        hp.runs[0].bold = True
        hp.runs[0].font.color.rgb = AMBER
        hp.runs[0].font.size = Pt(9)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------- XLSX ----------
def build_xlsx(ctx):
    p, A = ctx["protocol"], ctx["A"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Results"
    ws.append(["Sample ID", "Measurement", "Unit", "Observation", "Min", "Max", "Result", "Source"])
    for r in A["rows"]:
        ws.append([r["id"], r["value"], r["unit"], r["observation"], p["min"], p["max"], r["label"], r["source"]])
    ws.append([])
    for label, v in [("Total samples", A["total"]), ("Passed samples", A["passed"]), ("Failed samples", A["failed"]),
                     ("Planned samples", len(p["samples"])), ("Missing planned samples", ", ".join(A["missing"]) or "None")]:
        ws.append([label, v])
    ws.append([])
    ws.append([DRAFT])
    head = PatternFill("solid", fgColor="E5E5EA")
    for c in ws[1]:
        c.font, c.fill = Font(bold=True), head
    for row in ws.iter_rows(min_row=2, max_row=1 + len(A["rows"])):
        for c in row[4:6]:
            c.number_format = "0.00"
        row[1].number_format = "0.00"
        res = row[6]
        if res.value != "Pass":
            res.font = Font(bold=True, color="C4161C" if res.value == "Fail" else "B25000")
            res.fill = PatternFill("solid", fgColor="FDE8E8" if res.value == "Fail" else "FFF3D6")
    base = A["total"] + 3
    for i in range(5):
        ws.cell(base + i, 1).font = Font(bold=True)
    ws.cell(base + 6, 1).font = Font(bold=True, color="B25000")
    for col, w in zip("ABCDEFGH", [16, 13, 8, 24, 8, 8, 10, 22]):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------- PDF ----------
_ST = getSampleStyleSheet()
_BODY = ParagraphStyle("body", parent=_ST["BodyText"], fontSize=10, leading=14)
_CELL = ParagraphStyle("cell", parent=_BODY, fontSize=9, leading=11)


def _footer(c, d):
    c.saveState()
    c.setFont("Helvetica", 8)
    c.setFillColor(colors.HexColor("#b25000"))
    c.drawString(36, 20, DRAFT)
    c.drawRightString(A4[0] - 36, 20, f"Page {d.page}")
    c.restoreState()


def _mark(text):
    t = esc(text)
    return re.sub(r"(\{\{\w+\}\})", r'<font backColor="#fff3d6" color="#8a5a00">\1</font>', t)


def _docx_flowables(docx_bytes):
    """Render the filled DOCX in order, so the PDF always matches the DOCX."""
    doc = Document(io.BytesIO(docx_bytes))
    out = [Paragraph(DRAFT, ParagraphStyle("d", parent=_BODY, textColor=colors.HexColor("#b25000"), fontName="Helvetica-Bold")), Spacer(1, 6)]
    width = A4[0] - 72
    for el in doc.element.body.iterchildren():
        if el.tag.endswith("}p"):
            par = DPar(el, doc)
            if not par.text.strip():
                continue
            sid = (par._p.style or "").lower()   # style id, works even if the template defines no styles
            style = _ST["Title"] if sid == "title" else _ST["Heading2"] if sid.startswith("heading") else _BODY
            out.append(Paragraph(_mark(par.text).replace("\n", "<br/>"), style))
        elif el.tag.endswith("}tbl"):
            t = DTable(el, doc)
            data = [[Paragraph(_mark(c.text), _CELL) for c in r.cells] for r in t.rows]
            tbl = Table(data, colWidths=[width / len(data[0])] * len(data[0]), repeatRows=1)
            tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5e5ea")),
                                     ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c7c7cc")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            out += [Spacer(1, 4), tbl, Spacer(1, 8)]
    return out


def _render(story):
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=40, bottomMargin=40).build(story, onFirstPage=_footer, onLaterPages=_footer)
    return PdfReader(io.BytesIO(buf.getvalue()))


def _stamped(pdf_bytes):
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages = list(reader.pages)
    except Exception:
        raise ValueError("The calibration PDF could not be read.")
    for page in pages:
        w, h = float(page.mediabox.width), float(page.mediabox.height)
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(w, h))
        c.setFont("Helvetica-Bold", 10)
        c.drawRightString(w - 24, h - 24, "Attachment A")
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.HexColor("#b25000"))
        c.drawString(24, 12, DRAFT)
        c.save()
        page.merge_page(PdfReader(io.BytesIO(buf.getvalue())).pages[0])
    return pages


def build_pdf(ctx, docx_bytes):
    p, A = ctx["protocol"], ctx["A"]
    w = PdfWriter()
    for pg in _render(_docx_flowables(docx_bytes)).pages:
        w.add_page(pg)
    if ctx["has_pdf"]:
        for pg in _stamped(ctx["pdf_bytes"]):
            w.add_page(pg)
    else:
        for pg in _render([Paragraph("Attachment A", _ST["Title"]), Paragraph("Calibration record: not supplied. Nothing has been substituted.", _BODY)]).pages:
            w.add_page(pg)
    rows = [["Sample", "Measurement", "Unit", "Observation", "Min", "Max", "Result"]]
    rows += [[r["id"], fmt(r["value"]), r["unit"], r["observation"], f"{p['min']:.2f}", f"{p['max']:.2f}", r["label"]] for r in A["rows"]]
    t = Table([[Paragraph(esc(str(c)), _CELL) for c in r] for r in rows], repeatRows=1, colWidths=[58, 70, 40, 130, 45, 45, 60])
    style = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5e5ea")), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c7c7cc"))]
    style += [("TEXTCOLOR", (0, i + 1), (-1, i + 1), colors.HexColor("#c4161c")) for i, r in enumerate(A["rows"]) if r["status"] == "fail"]
    t.setStyle(TableStyle(style))
    summ = Table([["Total samples", A["total"]], ["Passed samples", A["passed"]], ["Failed samples", A["failed"]]], colWidths=[120, 60])
    summ.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c7c7cc"))]))
    story = [Paragraph("Attachment B", _ST["Title"]), Paragraph("Results table, rendered from Results.xlsx", _BODY), Spacer(1, 10), t, Spacer(1, 12), summ]
    for pg in _render(story).pages:
        w.add_page(pg)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


# ---------- package ----------
def build_all(ctx):
    docx = build_docx(ctx)
    out = {"Report.docx": docx, "Report_combined.pdf": build_pdf(ctx, docx), "Attachment B - Results.xlsx": build_xlsx(ctx)}
    if ctx["has_pdf"]:
        out[f"Attachment A - {ctx['pdf_name']}"] = ctx["pdf_bytes"]
    return out


def build_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()