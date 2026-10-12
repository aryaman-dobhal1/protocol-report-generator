async function buildForm(inp, vals) {
  const fd = new FormData()
  fd.append('protocol', new Blob([JSON.stringify(inp.protocol)], { type: 'application/json' }), 'protocol.json')
  fd.append('results', new Blob([inp.csv], { type: 'text/csv' }), inp.csvName || 'results.csv')
  inp.docs.forEach(d => fd.append('references', new Blob([d.text], { type: 'text/markdown' }), d.name))
  if (inp.pdf) fd.append('calibration', await (await fetch(inp.pdf.url)).blob(), inp.pdf.name)
  ;(inp.images || []).forEach(i => fd.append('images', i.file, i.name))   // evidence photos
  fd.append('overrides', JSON.stringify(vals))   // edited measurements
  return fd
}
const fail = async res => { throw new Error((await res.json().catch(() => ({}))).detail || 'Request failed') }

// Returns the generated file (docx | xlsx | pdf | zip) as a Blob.
export async function exportViaBackend(inp, vals, summary, format) {
  const fd = await buildForm(inp, vals)
  fd.append('summary', summary)   // the reviewed summary text
  fd.append('format', format)
  const res = await fetch('/api/export', { method: 'POST', body: fd })
  return res.ok ? res.blob() : fail(res)
}

// Asks the backend for a Groq-written summary grounded in the retrieved passages.
// source is 'ai', or 'rules (...)' with the reason when the AI draft was unavailable or rejected.
export async function draftWithAi(inp, vals) {
  const fd = await buildForm(inp, vals)
  fd.append('use_ai', 'true')
  const res = await fetch('/api/analyze', { method: 'POST', body: fd })
  if (!res.ok) return fail(res)
  const j = await res.json()
  return { text: j.summary, source: j.summary_source }
}