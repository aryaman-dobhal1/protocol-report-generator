const words = t => t.toLowerCase().match(/[a-z]{4,}/g) || []
const f2 = v => (v == null ? 'missing' : v.toFixed(2))
export const fmt = f2

export function parseCSV(text) {
  const [head, ...lines] = text.trim().split(/\r?\n/).map(l => l.split(',').map(s => s.trim()))
  return lines.filter(l => l.some(Boolean)).map(l => Object.fromEntries(head.map((h, i) => [h, l[i] ?? ''])))
}

// Split markdown into paragraph chunks with a section reference (e.g. "§2.1").
export function chunkDocs(docs) {
  return docs.flatMap(d => {
    let sec = '', n = 0
    const out = []
    d.text.split(/\n\s*\n/).forEach(block => {
      const lines = block.trim().split('\n')
      while (lines.length && lines[0].trim().startsWith('#')) {   // heading lines, possibly followed directly by text
        const h = lines.shift().trim()
        if (/^#{2,}\s/.test(h)) { sec = h.replace(/^#+\s*/, '').split(' ')[0]; n = 0 }
      }
      const text = lines.map(l => l.trim()).join(' ').trim()
      if (!text) return
      n++
      out.push({ file: d.name, ref: sec ? `§${sec}${n > 1 ? ` ¶${n}` : ''}` : `¶${n}`, text })
    })
    return out
  })
}

// Local keyword index: inverse-document-frequency weighted overlap, length normalised.
export function retrieve(chunks, query, k = 2) {
  const toks = chunks.map(c => words(c.text)), df = {}
  toks.forEach(t => new Set(t).forEach(w => (df[w] = (df[w] || 0) + 1)))
  const q = new Set(words(query))
  return chunks
    .map((c, i) => ({ ...c, score: toks[i].filter(w => q.has(w)).reduce((s, w) => s + Math.log(1 + chunks.length / df[w]), 0) / Math.sqrt(toks[i].length || 1) }))
    .filter(c => c.score > 0).sort((a, b) => b.score - a.score).slice(0, k)
}

export function analyze(inp, vals = {}) {
  const P = inp.protocol
  const rows = parseCSV(inp.csv).map(r => {
    const raw = vals[r.sample_id] ?? r.measurement
    const v = raw === '' || isNaN(+raw) ? null : +raw
    const st = v == null ? 'missing' : v >= P.min && v <= P.max ? 'pass' : 'fail'
    return { id: r.sample_id, value: v, unit: r.unit, obs: r.observation, st, label: { pass: 'Pass', fail: 'Fail', missing: 'Missing' }[st] }
  })
  const ids = rows.map(r => r.id)
  const missing = P.samples.filter(s => !ids.includes(s)), extra = ids.filter(s => !P.samples.includes(s))
  const passed = rows.filter(r => r.st === 'pass').length, failed = rows.length - passed
  const failing = rows.filter(r => r.st === 'fail'), noValue = rows.filter(r => r.st === 'missing')
  const badUnit = rows.filter(r => r.unit && r.unit !== P.unit)

  const chunks = chunkDocs(inp.docs)
  const method = retrieve(chunks, 'how wall thickness is measured micrometer conditions recording sample method', 2)
  const guide = retrieve(chunks, 'reporting results outside range missing evidence calibration record attachments', 2)
  const passages = [...method, ...guide].filter((x, i, a) => a.findIndex(y => y.text === x.text) === i)

  const unfilled = inp.pdf ? [] : ['{{calibration_ref}}']
  const range = `${f2(P.min)} to ${f2(P.max)} ${P.unit}`
  const checks = [
    [missing.length ? 'warn' : 'ok', 'Sample count', `${rows.length} of ${P.samples.length} planned samples${missing.length ? `, missing ${missing.join(', ')}` : ''}`],
    [missing.length || extra.length ? 'warn' : 'ok', 'Sample IDs', missing.length || extra.length ? [missing.length && `Missing ${missing.join(', ')}`, extra.length && `Unplanned ${extra.join(', ')}`].filter(Boolean).join('. ') : 'Planned and actual IDs match'],
    [noValue.length ? 'bad' : 'ok', 'Measurements', noValue.length ? `No value for ${noValue.map(r => r.id).join(', ')}` : 'Every sample has a value'],
    [badUnit.length ? 'bad' : 'ok', 'Units', badUnit.length ? `${badUnit.map(r => r.id).join(', ')} not in ${P.unit}, conflicts with protocol` : `All results in ${P.unit}`],
    [inp.pdf ? 'ok' : 'bad', 'Required attachments', inp.pdf ? 'Calibration record supplied' : 'Calibration record missing'],
    [failing.length ? 'bad' : 'ok', 'Acceptance range', failing.length ? `${failing.map(r => `${r.id} at ${f2(r.value)}`).join(', ')} outside ${range}` : `All results within ${range}`],
    [unfilled.length ? 'warn' : 'ok', 'Template placeholders', unfilled.length ? `${unfilled.length} unfilled: ${unfilled.join(', ')}` : 'None unfilled'],
    [chunks.length ? 'ok' : 'bad', 'Reference documents', chunks.length ? `${inp.docs.length} documents, ${chunks.length} passages indexed` : 'No reference documents loaded']]

  // Narrative drafted only from verified facts above. It never states causes.
  let s = `${rows.length} of ${P.samples.length} planned samples were measured. ${passed} met the acceptance range of ${range} and ${failed} did not.`
  failing.forEach(r => (s += ` ${r.id} measured ${f2(r.value)} ${r.unit}, ${r.value > P.max ? 'above' : 'below'} the range, and is reported as Fail (observation: ${r.obs.toLowerCase()}). The supplied evidence gives no cause.`))
  noValue.forEach(r => (s += ` ${r.id} has no measurement.`))
  if (missing.length) s += ` ${missing.join(', ')} ${missing.length > 1 ? 'were' : 'was'} planned but no result was supplied.`
  if (!inp.pdf) s += ' The calibration record (Attachment A) was not supplied, so equipment calibration could not be verified.'
  if (method[0]) s += ` Method context is taken from ${method[0].file}, ${method[0].ref}.`
  return { rows, missing, extra, passed, failed, total: rows.length, checks, passages, summary: s, range, unfilled }
}

export const calText = (p, pdf) => (pdf ? (p.calibrationRef ? `${p.calibrationRef} (Attachment A)` : 'See Attachment A') : null)

// Accepts protocol.json, or Markdown/text with "key: value" lines (for example "Acceptance range: 2.40 to 2.60").
export function parseProtocol(name, text) {
  let o
  if (/\.json$/i.test(name)) o = JSON.parse(text)
  else {
    o = {}
    text.split(/\r?\n/).forEach(l => { const m = l.match(/^[\s>*-]*\**([A-Za-z_ ]+?)\**\s*:\s*(.+)$/); if (m) o[m[1]] = m[2].trim() })
  }
  o = Object.fromEntries(Object.entries(o).map(([k, v]) => [k.toLowerCase().replace(/[\s_]/g, ''), v]))
  let min = o.min === undefined ? NaN : +o.min, max = o.max === undefined ? NaN : +o.max
  const r = o.acceptancerange || o.range
  if (r) { const n = String(r).match(/-?\d+(\.\d+)?/g) || []; if (n.length >= 2) { min = +n[0]; max = +n[1] } }
  const samples = Array.isArray(o.samples) ? o.samples : String(o.samples || '').split(/[,\s]+/).filter(Boolean)
  if (!isFinite(min) || !isFinite(max) || !samples.length) throw new Error('Protocol needs an acceptance range (min and max) and a list of sample IDs.')
  return { project: o.project || 'Not specified', component: o.component || 'Not specified', method: o.method || 'Not specified',
    unit: o.unit || 'mm', min, max, samples, calibrationRef: o.calibrationref || '',
    fixedStatement: o.fixedstatement || 'This report was prepared in accordance with the approved protocol.' }
}