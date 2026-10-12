"""Image-section tests. Run from backend/:  python tests/test_images.py"""
import io, os, sys, zipfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from docx import Document
from fastapi.testclient import TestClient
from pypdf import PdfReader
from app.main import app

T = os.environ.get("TEST_INPUTS", os.path.join(os.path.dirname(__file__), "..", "..", "test-inputs"))
client = TestClient(app)
IDS = ["S-001", "S-002", "S-003", "S-004", "S-005"]


def files(case="case-1-complete", imgs=(), extra=()):
    sh = os.path.join(T, "shared")
    f = [("protocol", ("protocol.json", open(f"{sh}/protocol.json", "rb"))),
         ("results", ("results.csv", open(f"{T}/{case}/results.csv", "rb"))),
         ("calibration", ("calibration-record.pdf", open(f"{sh}/calibration-record.pdf", "rb")))]
    f += [("references", (n, open(f"{sh}/reference/{n}", "rb"))) for n in sorted(os.listdir(f"{sh}/reference"))]
    f += [("images", (f"{i}.jpg", open(f"{T}/images/{i}.jpg", "rb"))) for i in imgs]
    return f + list(extra)


def export(f):
    r = client.post("/api/export", files=f, data={"format": "zip", "summary": "Draft summary."})
    assert r.status_code == 200, r.text
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    doc = Document(io.BytesIO(zf.read("Report.docx")))
    text = "\n".join(p.text for p in doc.paragraphs)
    pdf = PdfReader(io.BytesIO(zf.read("Report_combined.pdf")))
    return zf, doc, text, pdf


def check(f):
    j = client.post("/api/analyze", files=f).json()
    return next(c for c in j["checks"] if c["title"] == "Photographic evidence"), j


# 1. no images: output unchanged, check is ok and optional
c, j = check(files())
assert c["status"] == "ok" and "optional" in c["detail"], c
zf0, doc0, text0, pdf0 = export(files())
assert len(doc0.inline_shapes) == 0 and "Photographic evidence" not in text0
assert not [n for n in zf0.namelist() if "Images/" in n]
print("no images ok")

# 2. all five photos: embedded in DOCX and PDF, originals in ZIP, captions carry the calculated result
f = files(imgs=IDS)
c, j = check(f)
assert c["status"] == "ok" and "5 images" in c["detail"], c
zf, doc, text, pdf = export(files(imgs=IDS))
assert len(doc.inline_shapes) == 5 and "Photographic evidence (Attachment C)" in text
assert "Figure 3. S-003, 2.45 mm, Pass (S-003.jpg)" in text, text
assert sum(len(p.images) for p in pdf.pages) == 5 and len(pdf.pages) > len(pdf0.pages)
assert sorted(n for n in zf.namelist() if "Images/" in n) == [f"Attachment C - Images/{i}.jpg" for i in IDS]
print("5 photos ok, pdf pages", len(pdf0.pages), "->", len(pdf.pages))

# 3. failing case: the photo caption shows Fail, same as the table
_, doc, text, _ = export(files("case-2-failing", IDS))
assert "Figure 3. S-003, 2.71 mm, Fail (S-003.jpg)" in text
print("failing caption ok")

# 4. one planned sample without a photo: flagged in checklist and report, nothing invented
c, j = check(files(imgs=IDS[:3] + IDS[4:]))
assert c["status"] == "warn" and "No photo for S-004" in c["detail"], c
_, doc, text, _ = export(files(imgs=IDS[:3] + IDS[4:]))
assert "No photograph supplied for: S-004" in text and len(doc.inline_shapes) == 4
print("missing photo flagged")

# 5. image that matches no sample: still included, flagged as unlinked
extra = [("images", ("site-overview.jpg", open(f"{T}/images/S-001.jpg", "rb")))]
c, j = check(files(imgs=IDS, extra=extra))
assert c["status"] == "warn" and "site-overview.jpg" in c["detail"], c
_, doc, text, _ = export(files(imgs=IDS, extra=extra))
assert "Unlinked image" in text and len(doc.inline_shapes) == 6
print("unlinked image flagged")

# 6. not an image: clear 422, no crash
r = client.post("/api/analyze", files=files() + [("images", ("S-001.jpg", io.BytesIO(b"not an image")))])
assert r.status_code == 422 and "could not be read" in r.json()["detail"], r.text
print("bad image rejected")
print("all image tests passed")