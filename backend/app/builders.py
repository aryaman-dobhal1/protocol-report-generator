"""Output files: DOCX (filled from the template), XLSX, combined PDF, ZIP."""
import copy, io, re, zipfile
from xml.sax.saxutils import escape as esc

from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from docx.shared import Inches, Pt, RGBColor
from docx.table import Table as DTable
from docx.text.paragraph import Paragraph as DPar
from openpyxl import Workbook
from PIL import Image as PILImage, ImageDraw, ImageOps
from openpyxl.styles import Alignment, Font, PatternFill
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import Flowable, Image as RLImage, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

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
    d.add_paragraph("Full dataset: Attachment B (Results.xlsx). Calibration record: Attachment A.")
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


MAX_ROWS = 25          # above this the report shows exceptions only; the full dataset is an attached object
_NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
       'xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office"')


def _shade(cell, fill):
    cell._tc.get_or_add_tcPr().append(parse_xml(f'<w:shd {nsdecls("w")} w:val="clear" w:color="auto" w:fill="{fill}"/>'))


def _table_polish(t, widths, header=True, size=9.5):
    """Fixed column widths, tight cell spacing, shaded repeating header, rows that never split across pages."""
    t.autofit = False
    for row in t.rows:
        row._tr.get_or_add_trPr().append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))
        for c, w in zip(row.cells, widths):
            c.width = Inches(w)
            for par in c.paragraphs:
                par.paragraph_format.space_before = par.paragraph_format.space_after = Pt(2)
                for r in par.runs:
                    r.font.size = Pt(size)
    if header:
        t.rows[0]._tr.get_or_add_trPr().append(parse_xml(f'<w:tblHeader {nsdecls("w")}/>'))
        for c in t.rows[0].cells:
            _shade(c, "E5E5EA")


def _icon_png(label="XLSX"):
    im = PILImage.new("RGBA", (96, 96), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((8, 4, 88, 92), 8, fill=(33, 115, 70, 255))
    for x in (26, 48, 70):
        d.line((x, 18, x, 56), fill=(255, 255, 255, 200), width=2)
    for y in (18, 31, 44, 56):
        d.line((20, y, 76, y), fill=(255, 255, 255, 200), width=2)
    d.text((48, 74), label, fill=(255, 255, 255, 255), anchor="mm")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _embed_xlsx(doc, blob):
    """Embed the workbook as a real Word object (icon; double-click opens it in Excel). Returns the paragraph."""
    part = Part(PackURI("/word/embeddings/Microsoft_Excel_Sheet1.xlsx"),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", blob, doc.part.package)
    rid_pkg = doc.part.relate_to(part, RT.PACKAGE)
    rid_img, _ = doc.part.get_or_add_image(io.BytesIO(_icon_png()))
    par = doc.add_paragraph()
    par._p.append(parse_xml(
        f'<w:r {_NS}><w:object w:dxaOrig="960" w:dyaOrig="960">'
        '<v:shapetype id="_x0000_t75" coordsize="21600,21600" o:spt="75" o:preferrelative="t" path="m@4@5l@4@11@9@11@9@5xe" filled="f" stroked="f">'
        '<v:stroke joinstyle="miter"/><v:formulas><v:f eqn="if lineDrawn pixelLineWidth 0"/><v:f eqn="sum @0 1 0"/><v:f eqn="sum 0 0 @1"/>'
        '<v:f eqn="prod @2 1 2"/><v:f eqn="prod @3 21600 pixelWidth"/><v:f eqn="prod @3 21600 pixelHeight"/><v:f eqn="sum @0 0 1"/>'
        '<v:f eqn="prod @6 1 2"/><v:f eqn="prod @7 21600 pixelWidth"/><v:f eqn="sum @8 21600 0"/><v:f eqn="prod @7 21600 pixelHeight"/>'
        '<v:f eqn="sum @10 21600 0"/></v:formulas><v:path o:extrusionok="f" gradientshapeok="t" o:connecttype="rect"/>'
        '<o:lock v:ext="edit" aspectratio="t"/></v:shapetype>'
        f'<v:shape id="_x0000_i1025" type="#_x0000_t75" style="width:48pt;height:48pt" o:ole=""><v:imagedata r:id="{rid_img}" o:title=""/></v:shape>'
        f'<o:OLEObject Type="Embed" ProgID="Excel.Sheet.12" ShapeID="_x0000_i1025" DrawAspect="Icon" ObjectID="_1700000001" r:id="{rid_pkg}"/>'
        '</w:object></w:r>'))
    return par


def _results_block(doc, ctx):
    """Summary paragraph, compact table (all rows, or only exceptions for big datasets) and the embedded workbook.
    Elements are created at the end of the body and returned in order, so the caller can move them into place."""
    A, unit = ctx["A"], ctx["protocol"]["unit"]
    rows = [(r["id"], fmt(r["value"]), r["unit"], r["observation"], r["label"], r["status"]) for r in A["rows"]]
    rows += [(m, "-", "-", "No result supplied", "Missing", "missing") for m in A["missing"]]
    big = len(rows) > MAX_ROWS
    shown = [r for r in rows if r[-1] != "pass"][:MAX_ROWS] if big else rows
    els = []
    if ctx.get("results_summary"):
        els.append(doc.add_paragraph(ctx["results_summary"])._p)
    if big:
        note = doc.add_paragraph(f"{len(rows)} results in total. Showing {len(shown)} Fail/Missing "
                                 f"result{'s' if len(shown) != 1 else ''} only; every result is in the embedded workbook below.")
        note.runs[0].italic = True
        els.append(note._p)
    if shown:
        t = doc.add_table(rows=1, cols=5)
        _grid(t)
        for c, h in zip(t.rows[0].cells, ["Sample", f"Measurement ({unit})", "Unit", "Observation", "Result"]):
            c.text = h
            c.paragraphs[0].runs[0].bold = True
        for *vals, st in shown:
            cells = t.add_row().cells
            for c, v in zip(cells, vals):
                c.text = str(v)
            if st != "pass":
                run = cells[4].paragraphs[0].runs[0]
                run.bold = True
                run.font.color.rgb = RGBColor(0xC4, 0x16, 0x1C) if st == "fail" else AMBER
        _table_polish(t, [0.9, 1.4, 0.6, 2.4, 0.9])
        els.append(t._tbl)
    els.append(_embed_xlsx(doc, build_xlsx(ctx))._p)
    cap = doc.add_paragraph(f"Attachment B: Results.xlsx, the full dataset ({len(rows)} rows), is embedded in this document. "
                            "Double-click the icon to open it.")
    cap.runs[0].italic, cap.runs[0].font.size = True, Pt(9)
    els.append(cap._p)
    return els


def prepare_images(files):
    """Validate uploads and normalise them (EXIF rotation, max 1600 px, JPEG) so reports stay small."""
    out = []
    for name, data in files:
        try:
            im = ImageOps.exif_transpose(PILImage.open(io.BytesIO(data))).convert("RGB")
        except Exception:
            raise ValueError(f"Image '{name}' could not be read. Use PNG or JPEG files.")
        im.thumbnail((1600, 1600))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        out.append({"name": name, "orig": data, "jpg": buf.getvalue(), "w": im.width, "h": im.height, "sample": None})
    return out


def _images_section(doc, ctx):
    """Evidence photos exactly as supplied, three per row. Captions come from the calculated results; AI never describes images."""
    p, A, imgs = ctx["protocol"], ctx["A"], ctx.get("images") or []
    if not imgs:
        return
    _heading(doc, "Photographic evidence (Attachment C)")
    intro = doc.add_paragraph("Photographs are shown as supplied and are not interpreted by AI. Each caption states the calculated result for that sample.")
    intro.paragraph_format.keep_with_next = True
    order = {s: i for i, s in enumerate(p["samples"])}
    rows = {r["id"]: r for r in A["rows"]}
    ordered = sorted(imgs, key=lambda i: (order.get(i["sample"], 999), i["name"]))
    cols = 3
    t = doc.add_table(rows=0, cols=cols)
    for start in range(0, len(ordered), cols):
        cells = t.add_row().cells
        for c, (n, im) in zip(cells, enumerate(ordered[start:start + cols], start + 1)):
            r = rows.get(im["sample"])
            what = ("Unlinked image" if im["sample"] is None else
                    f"{im['sample']}, {fmt(r['value'])} {r['unit']}, {r['label']}" if r else f"{im['sample']}, no result supplied")
            c.paragraphs[0].add_run().add_picture(io.BytesIO(im["jpg"]), width=Inches(min(2.05, 1.5 * im["w"] / im["h"])))
            cap = c.add_paragraph()
            run = cap.add_run(f"Fig. {n}. {what}")
            run.italic, run.font.size = True, Pt(8)
            run.add_break()
            small = cap.add_run(im["name"])
            small.font.size = Pt(7)
    _table_polish(t, [2.15] * cols, header=False, size=8)
    lacking = [s for s in p["samples"] if s not in {i["sample"] for i in imgs}]
    if lacking:
        run = doc.add_paragraph().add_run("No photograph supplied for: " + ", ".join(lacking))
        run.bold, run.font.color.rgb = True, AMBER


def build_docx(ctx):
    p, A = ctx["protocol"], ctx["A"]
    doc = Document(io.BytesIO(ctx["template"])) if ctx["template"] else default_template(p["fixed_statement"])
    if not ctx["template"]:
        doc.styles["Normal"].paragraph_format.space_after = Pt(4)
    vals = placeholder_values(p, A["range"], A["passages"], ctx["has_pdf"], ctx["summary"])
    embedded = False
    for par in list(doc.paragraphs):           # protected wording is untouched: only paragraphs with {{...}} change
        text, keys = par.text, PLACEHOLDER.findall(par.text)
        if not keys:
            continue
        if "results_table" in keys:
            ref = par._p
            for el in _results_block(doc, ctx):     # summary, compact table, embedded workbook, in place of the placeholder
                ref.addnext(el)
                ref = el
            par._p.getparent().remove(par._p)
            embedded = True
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
    if not embedded:                            # template without {{results_table}}: still attach the workbook
        _heading(doc, "Results overview (Attachment B)")
        _results_block(doc, ctx)
    _images_section(doc, ctx)
    _heading(doc, "Attachment index")
    t = doc.add_table(rows=1, cols=3)
    _grid(t)
    for c, h in zip(t.rows[0].cells, ["Label", "Document", "Status"]):
        c.text = h
        c.paragraphs[0].runs[0].bold = True
    for row in [("Attachment A", "Calibration record (supporting PDF)", "Included" if ctx["has_pdf"] else "Not supplied"),
                ("Attachment B", "Results dataset (Results.xlsx, embedded object)", "Included"),
                *([("Attachment C", f"Photographic evidence ({len(ctx['images'])} images)", "Included")] if ctx.get("images") else [])]:
        for c, v in zip(t.add_row().cells, row):
            c.text = v
    _table_polish(t, [1.2, 3.8, 1.2])
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
    sm = wb.create_sheet("Summary")
    sm["A1"], sm["A1"].font = "Results overview", Font(bold=True, size=13)
    sm["A2"], sm["A2"].alignment = ctx.get("results_summary") or "", Alignment(wrap_text=True, vertical="top")
    sm.column_dimensions["A"].width = 110
    sm.row_dimensions[2].height = 90
    sm["A4"], sm["A4"].font = DRAFT, Font(bold=True, color="B25000")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------- PDF ----------
_ST = getSampleStyleSheet()
_BODY = ParagraphStyle("body", parent=_ST["BodyText"], fontSize=10, leading=14, spaceAfter=6)
_CAP = ParagraphStyle("cap", parent=_ST["BodyText"], fontSize=8, leading=10, spaceAfter=0)
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


class _AttachBox(Flowable):
    """Attachment card. It records where it lands so a clickable file-attachment icon can be placed on it."""
    H = 64

    def __init__(self, rows, width, sink):
        super().__init__()
        self.rows, self.width, self.sink = rows, width, sink

    def wrap(self, aw, ah):
        return self.width, self.H

    def draw(self):
        c, w, h = self.canv, self.width, self.H
        green = colors.HexColor("#217346")
        c.saveState()
        c.setFillColor(colors.HexColor("#f2f9f5"))
        c.setStrokeColor(green)
        c.setLineWidth(0.8)
        c.roundRect(0, 0, w, h, 8, fill=1, stroke=1)
        c.setFillColor(green)                                    # spreadsheet icon tile
        c.roundRect(10, 10, 44, 44, 6, fill=1, stroke=0)
        c.setStrokeColor(colors.white)
        c.setLineWidth(1)
        for x in (24, 32, 40):
            c.line(x, 28, x, 48)
        for y in (28, 34.5, 41, 48):
            c.line(17, y, 47, y)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 7)
        c.drawCentredString(32, 15, "XLSX")
        c.setFillColor(colors.HexColor("#14492c"))
        c.setFont("Helvetica-Bold", 11)
        c.drawString(64, h - 24, "Attachment B: Results.xlsx")
        c.setFillColor(colors.HexColor("#555555"))
        c.setFont("Helvetica", 9)
        c.drawString(64, h - 38, f"Full dataset, {self.rows} rows")
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.HexColor("#777777"))
        c.drawString(64, 13, "Click the paperclip icon, or use your PDF viewer's attachments panel.")
        c.setFont("Helvetica", 6.5)
        c.drawCentredString(w - 30, 12, "Click to open")
        c.restoreState()
        x, y = c.absolutePosition(w - 44, 24)
        self.sink.append((c.getPageNumber() - 1, x, y, 28, 28))


def _docx_flowables(docx_bytes, n_rows=0, sink=None):
    """Render the filled DOCX in order, so the PDF always matches the DOCX."""
    doc = Document(io.BytesIO(docx_bytes))
    out = [Paragraph(DRAFT, ParagraphStyle("d", parent=_BODY, textColor=colors.HexColor("#b25000"), fontName="Helvetica-Bold")), Spacer(1, 4)]
    width = A4[0] - 72
    for el in doc.element.body.iterchildren():
        if el.tag.endswith("}p"):
            par = DPar(el, doc)
            if el.xpath('.//*[local-name()="OLEObject"]'):          # embedded workbook: show a clear attachment box
                out += [Spacer(1, 6), _AttachBox(n_rows, width, sink if sink is not None else []), Spacer(1, 8)]
                continue
            if par.text.startswith("Attachment B: Results.xlsx"):   # the DOCX caption says 'double-click'; the box above replaces it
                continue
            for rid in el.xpath(".//a:blip/@r:embed"):
                blob = doc.part.related_parts[rid].blob
                iw, ih = PILImage.open(io.BytesIO(blob)).size
                w = min(300, 230 * iw / ih)
                out += [RLImage(io.BytesIO(blob), width=w, height=w * ih / iw, hAlign="LEFT"), Spacer(1, 4)]
            if not par.text.strip():
                continue
            sid = (par._p.style or "").lower()   # style id, works even if the template defines no styles
            style = _ST["Title"] if sid == "title" else ParagraphStyle("h", parent=_ST["Heading2"], keepWithNext=1) if sid.startswith("heading") else _BODY
            if par.text.startswith("Photographs are shown"):      # keep heading + intro with the photo grid
                style = ParagraphStyle("kwn", parent=_BODY, keepWithNext=1)
            out.append(Paragraph(_mark(par.text).replace("\n", "<br/>"), style))
        elif el.tag.endswith("}tbl"):
            t = DTable(el, doc)
            n = len(t.rows[0].cells)
            if el.xpath(".//a:blip"):                                  # photo grid: images and captions inside each cell
                cw = width / n

                def cell(c):
                    fl = []
                    for cp in c.paragraphs:
                        for rid in cp._p.xpath(".//a:blip/@r:embed"):
                            blob = doc.part.related_parts[rid].blob
                            iw, ih = PILImage.open(io.BytesIO(blob)).size
                            w = min(cw - 14, 150 * iw / ih)
                            fl.append(RLImage(io.BytesIO(blob), width=w, height=w * ih / iw, hAlign="LEFT"))
                        if cp.text.strip():
                            fl.append(Paragraph(_mark(cp.text).replace("\n", "<br/>"), _CAP))
                    return fl or [Spacer(1, 1)]
                data = [[cell(c) for c in r.cells] for r in t.rows]
                mk = lambda d: Table(d, colWidths=[cw] * n, style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
                lead = []
                while out and isinstance(out[-1], Paragraph) and getattr(out[-1].style, "keepWithNext", 0):
                    lead.insert(0, out.pop())            # heading + intro never get stranded away from the first photo row
                out.append(KeepTogether(lead + [Spacer(1, 4), mk(data[:1])]))
                if data[1:]:
                    out.append(mk(data[1:]))
                out.append(Spacer(1, 6))
                continue
            data = [[Paragraph(_mark(c.text), _CELL) for c in r.cells] for r in t.rows]
            lens = [min(max(max(len(r.cells[i].text) for r in t.rows), 6), 38) for i in range(n)]     # widths follow content
            tbl = Table(data, colWidths=[width * x / sum(lens) for x in lens], repeatRows=1)
            tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5e5ea")),
                                     ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c7c7cc")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            out += [Spacer(1, 2), tbl, Spacer(1, 8)]
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
    A = ctx["A"]
    w, sink = PdfWriter(), []
    for pg in _render(_docx_flowables(docx_bytes, A["total"] + len(A["missing"]), sink)).pages:
        w.add_page(pg)
    if ctx["has_pdf"]:                                       # Attachment A pages; "not supplied" is stated in the report itself
        for pg in _stamped(ctx["pdf_bytes"]):
            w.add_page(pg)
    try:                                                     # Attachment B travels inside the PDF as a file attachment
        w.add_attachment("Results.xlsx", build_xlsx(ctx))
        fs = w._root_object["/Names"]["/EmbeddedFiles"]["/Names"][-1]      # the file spec just added
        for pno, x, y, bw, bh in sink:                       # clickable paperclip icon on the Attachment B card
            ref = w._add_object(DictionaryObject({
                NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/FileAttachment"),
                NameObject("/Rect"): ArrayObject([FloatObject(v) for v in (x, y, x + bw, y + bh)]),
                NameObject("/FS"): fs, NameObject("/Name"): NameObject("/Paperclip"),
                NameObject("/Contents"): TextStringObject("Results.xlsx: full dataset"),
                NameObject("/F"): NumberObject(4),
                NameObject("/C"): ArrayObject([FloatObject(0.13), FloatObject(0.45), FloatObject(0.27)])}))
            page = w.pages[pno]
            if "/Annots" not in page:
                page[NameObject("/Annots")] = ArrayObject()
            page["/Annots"].get_object().append(ref)
    except (AttributeError, KeyError, IndexError):          # older pypdf: the attachments panel still works if it was added
        pass
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


# ---------- package ----------
def build_all(ctx):
    docx = build_docx(ctx)
    out = {"Report.docx": docx, "Report_combined.pdf": build_pdf(ctx, docx), "Attachment B - Results.xlsx": build_xlsx(ctx)}
    if ctx["has_pdf"]:
        out[f"Attachment A - {ctx['pdf_name']}"] = ctx["pdf_bytes"]
    for im in ctx.get("images") or []:                       # originals, as uploaded
        name = f"Attachment C - Images/{im['name']}"
        while name in out:
            name = name.replace("Images/", "Images/dup-")
        out[name] = im["orig"]
    return out


def build_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()