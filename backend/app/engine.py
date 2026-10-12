"""Deterministic core: parsing, retrieval, pass/fail, validation, rule-based summary.
Nothing in this file calls an AI model. Official results come only from here."""
import csv, io, json, math, re

PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
DEFAULT_KEYS = ["project_id", "component", "test_method", "acceptance_range", "calibration_ref",
                "retrieved_guidance", "results_table", "summary"]
_WORD = re.compile(r"[a-z]{4,}")
FIXED = "This report was prepared in accordance with the approved protocol."


def fmt(v):
    return "missing" if v is None else f"{v:.2f}"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------- parsing ----------
def parse_protocol(name, text):
    """protocol.json, or Markdown/text with one 'key: value' per line."""
    if name.lower().endswith(".json"):
        raw = json.loads(text)
    else:
        raw = {}
        for line in text.splitlines():
            m = re.match(r"^[\s>*-]*\**([A-Za-z_ ]+?)\**\s*:\s*(.+)$", line)
            if m:
                raw[m.group(1)] = m.group(2).strip()
    o = {re.sub(r"[\s_]", "", k.lower()): v for k, v in raw.items()}
    lo, hi = _num(o.get("min")), _num(o.get("max"))
    rng = o.get("acceptancerange") or o.get("range")
    if rng:
        n = re.findall(r"-?\d+(?:\.\d+)?", str(rng))
        if len(n) >= 2:
            lo, hi = float(n[0]), float(n[1])
    s = o.get("samples")
    samples = s if isinstance(s, list) else [x for x in re.split(r"[,\s]+", str(s or "")) if x]
    if lo is None or hi is None or not samples:
        raise ValueError("Protocol needs an acceptance range (min and max) and a list of sample IDs.")
    return {"project": o.get("project") or "Not specified", "component": o.get("component") or "Not specified",
            "method": o.get("method") or o.get("testmethod") or "Not specified", "unit": o.get("unit") or "mm",
            "min": lo, "max": hi, "samples": samples, "calibration_ref": o.get("calibrationref") or "",
            "fixed_statement": o.get("fixedstatement") or FIXED}


def parse_results(text):
    rows = list(csv.DictReader(io.StringIO(text.strip())))
    rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in rows]
    if not rows or "sample_id" not in rows[0] or "measurement" not in rows[0]:
        raise ValueError("Results CSV needs the columns sample_id and measurement (plus unit, observation).")
    return rows


# ---------- retrieval over the reference documents ----------
def chunk_docs(docs):
    """Split markdown into paragraph chunks with a section reference such as '§2.1'."""
    out = []
    for name, text in docs:
        sec, n = "", 0
        for block in re.split(r"\n\s*\n", text):
            lines = block.strip().splitlines()
            while lines and lines[0].lstrip().startswith("#"):   # heading lines, possibly followed directly by text
                h = lines.pop(0).strip()
                if re.match(r"#{2,}\s", h):
                    sec, n = re.sub(r"^#+\s*", "", h).split(" ")[0], 0
            b = " ".join(l.strip() for l in lines).strip()
            if not b:
                continue
            n += 1
            ref = f"§{sec}" + (f" ¶{n}" if n > 1 else "") if sec else f"¶{n}"
            out.append({"file": name, "ref": ref, "text": b})
    return out


def retrieve(chunks, query, k=2):
    """Local keyword index: IDF-weighted overlap, length normalised."""
    toks = [_WORD.findall(c["text"].lower()) for c in chunks]
    df = {}
    for t in toks:
        for w in set(t):
            df[w] = df.get(w, 0) + 1
    q = set(_WORD.findall(query.lower()))
    scored = []
    for c, t in zip(chunks, toks):
        s = sum(math.log(1 + len(chunks) / df[w]) for w in t if w in q) / math.sqrt(len(t) or 1)
        if s > 0:
            scored.append({**c, "score": round(s, 3)})
    return sorted(scored, key=lambda x: -x["score"])[:k]


# ---------- placeholders ----------
def placeholder_values(p, rng, passages, has_pdf, summary):
    cal = None
    if has_pdf:
        cal = f"{p['calibration_ref']} (Attachment A)" if p["calibration_ref"] else "See Attachment A"
    guide = "\n".join(f"{x['text']} [{x['file']}, {x['ref']}]" for x in passages) or None
    return {"project_id": p["project"], "component": p["component"], "test_method": p["method"],
            "acceptance_range": rng, "calibration_ref": cal, "retrieved_guidance": guide,
            "results_table": "", "summary": summary}


def image_sample(name, planned):
    """Link an image to a planned sample by its file name, e.g. 'S-003.jpg' or 'S-003_edge.png'."""
    n = name.lower()
    for s in sorted(planned, key=len, reverse=True):
        if re.search(r"(?<![a-z0-9])%s(?![0-9])" % re.escape(s.lower()), n):
            return s
    return None


# ---------- analysis ----------
def analyze(p, result_rows, docs, has_pdf, template_keys=None, overrides=None, results_name="results.csv", image_names=None):
    overrides = overrides or {}
    lo, hi, unit = p["min"], p["max"], p["unit"]
    rng = f"{lo:.2f} to {hi:.2f} {unit}"
    rows = []
    for i, r in enumerate(result_rows):
        v = _num(overrides.get(r["sample_id"], r.get("measurement", "")))
        st = "missing" if v is None else "pass" if lo <= v <= hi else "fail"
        rows.append({"id": r["sample_id"], "value": v, "unit": r.get("unit", ""), "observation": r.get("observation", ""),
                     "status": st, "label": {"pass": "Pass", "fail": "Fail", "missing": "Missing"}[st],
                     "source": f"{results_name}, row {i + 2}"})
    ids = [r["id"] for r in rows]
    missing = [s for s in p["samples"] if s not in ids]
    extra = [s for s in ids if s not in p["samples"]]
    dupes = sorted({s for s in ids if ids.count(s) > 1})
    failing = [r for r in rows if r["status"] == "fail"]
    no_value = [r for r in rows if r["status"] == "missing"]
    bad_unit = [r for r in rows if r["unit"] and r["unit"] != unit]
    passed = sum(r["status"] == "pass" for r in rows)

    chunks = chunk_docs(docs)
    method = retrieve(chunks, "how wall thickness is measured micrometer conditions recording sample method", 2)
    guide = retrieve(chunks, "reporting results outside range missing evidence calibration record attachments", 2)
    passages = []
    for x in method + guide:
        if all(x["text"] != y["text"] for y in passages):
            passages.append(x)

    # Narrative facts come only from the verified values above. Never states causes.
    s = f"{len(rows)} of {len(p['samples'])} planned samples were measured. {passed} met the acceptance range of {rng} and {len(failing)} did not."
    for r in failing[:5]:
        s += (f" {r['id']} measured {fmt(r['value'])} {unit}, {'above' if r['value'] > hi else 'below'} the range, and is reported as Fail"
              f" (observation: {r['observation'].lower()}). The supplied evidence gives no cause.")
    if len(failing) > 5:
        s += f" {len(failing) - 5} further samples also failed; see Attachment B."
    for r in no_value[:5]:
        s += f" {r['id']} has no measurement."
    if len(no_value) > 5:
        s += f" {len(no_value) - 5} further samples have no measurement."
    if missing:
        s += f" {', '.join(missing)} {'were' if len(missing) > 1 else 'was'} planned but no result was supplied."
    if not has_pdf:
        s += " The calibration record (Attachment A) was not supplied, so equipment calibration could not be verified."
    if method:
        s += f" Method context is taken from {method[0]['file']}, {method[0]['ref']}."

    vals = placeholder_values(p, rng, passages, has_pdf, s)
    unfilled = ["{{%s}}" % k for k in (template_keys or DEFAULT_KEYS) if vals.get(k) is None]
    C = lambda ok, level: "ok" if ok else level
    images = [{"name": n, "sample": image_sample(n, p["samples"])} for n in (image_names or [])]
    no_photo = [s for s in p["samples"] if s not in {i["sample"] for i in images}]
    unlinked = [i["name"] for i in images if i["sample"] is None]
    fail_txt = ", ".join(f"{r['id']} at {fmt(r['value'])}" for r in failing[:5]) + (f" and {len(failing) - 5} more" if len(failing) > 5 else "")
    checks = [
        {"status": C(not missing, "warn"), "title": "Sample count",
         "detail": f"{len(rows)} of {len(p['samples'])} planned samples" + (f", missing {', '.join(missing)}" if missing else "")},
        {"status": C(not (missing or extra or dupes), "warn"), "title": "Sample IDs",
         "detail": ". ".join(filter(None, [missing and f"Missing {', '.join(missing)}", extra and f"Unplanned {', '.join(extra)}",
                                           dupes and f"Duplicate {', '.join(dupes)}"])) or "Planned and actual IDs match"},
        {"status": C(not no_value, "bad"), "title": "Measurements",
         "detail": f"No value for {', '.join(r['id'] for r in no_value)}" if no_value else "Every sample has a value"},
        {"status": C(not bad_unit, "bad"), "title": "Units",
         "detail": f"{', '.join(r['id'] for r in bad_unit)} not in {unit}, conflicts with protocol" if bad_unit else f"All results in {unit}"},
        {"status": C(has_pdf, "bad"), "title": "Required attachments",
         "detail": "Calibration record supplied" if has_pdf else "Calibration record missing"},
        {"status": C(not failing, "bad"), "title": "Acceptance range",
         "detail": f"{fail_txt} outside {rng}" if failing else f"All results within {rng}"},
        {"status": C(not unfilled, "warn"), "title": "Template placeholders",
         "detail": f"{len(unfilled)} unfilled: {', '.join(unfilled)}" if unfilled else "None unfilled"},
        {"status": C(chunks, "bad"), "title": "Reference documents",
         "detail": f"{len(docs)} documents, {len(chunks)} passages indexed" if chunks else "No reference documents loaded"},
        {"status": C(not images or not (no_photo or unlinked), "warn"), "title": "Photographic evidence",
         "detail": "No images supplied (optional)" if not images else ". ".join(filter(None, [
             f"{len(images)} images", no_photo and f"No photo for {', '.join(no_photo)}",
             unlinked and f"Not linked to a sample: {', '.join(unlinked)}"]))},
    ]
    return {"range": rng, "rows": rows, "missing": missing, "extra": extra, "total": len(rows), "passed": passed,
            "failed": len(failing), "no_value": len(no_value), "passages": passages, "checks": checks,
            "unfilled": unfilled, "summary": s, "images": images, "no_photo": no_photo if images else []}