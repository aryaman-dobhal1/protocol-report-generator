import { useState, useMemo, useEffect } from 'react'
import { preset } from './samples'
import { analyze, parseProtocol, calText } from './lib/engine'
import { exportViaBackend, draftWithAi } from './api'

const FILES = { docx: 'Report.docx', xlsx: 'Results.xlsx', pdf: 'Report_combined.pdf', zip: 'Report package.zip' }
const save = (blob, name) => {
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob); a.download = name; a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 1000)
}

const I = {
  doc: 'M7 3h7l5 5v13H7z M14 3v5h5', search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z M20 20l-4-4',
  table: 'M4 5h16v14H4z M4 10h16 M10 5v14', spark: 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z',
  sun: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z M12 2v2 M12 20v2 M4.9 4.9l1.4 1.4 M17.7 17.7l1.4 1.4 M2 12h2 M20 12h2 M4.9 19.1l1.4-1.4 M17.7 6.3l1.4-1.4',
  moon: 'M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z'
}
const Icon = ({ d, s = 20 }) => <svg viewBox="0 0 24 24" width={s} height={s} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={d} /></svg>
const Badge = ({ st }) => <span className={'badge ' + st}>{{ pass: 'Pass', fail: 'Fail', missing: 'Missing' }[st]}</span>
const Pick = ({ kind, accept, multiple, onFile, children }) => (
  <label className="link">{children}<input type="file" hidden accept={accept} multiple={multiple} onChange={e => onFile(kind, e)} /></label>
)
const Row = ({ title, meta, ok = true, kind, badge, children }) => (
  <div className="frow"><span className={'ft ' + (ok ? kind : 'miss')}>{badge}</span><div className="grow"><div>{title}</div><small>{meta}</small></div>{children}</div>
)
const Section = ({ title, tag, source, icon, c, children }) => (
  <section className={'card sec ' + c}><div className="sh"><span className="ico"><Icon d={I[icon]} /></span><div className="grow"><h2>{title}</h2><p className="source">{source}</p></div><span className="tag">{tag}</span></div>{children}</section>
)
const rng = P => `${P.min.toFixed(2)} to ${P.max.toFixed(2)} ${P.unit}`
const EMPTY = { csv: null, csvName: '', docs: [], pdf: null, protocol: null, protocolName: '' }

export default function App() {
  const [mine, setMine] = useState(EMPTY)
  const [vals, setVals] = useState({})
  const [gen, setGen] = useState(false)
  const [draft, setDraft] = useState(null)
  const [msg, setMsg] = useState('')
  const [theme, setTheme] = useState(() => {
    let t = null
    try { t = localStorage.getItem('theme') } catch {}
    return t || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
  })
  useEffect(() => { document.documentElement.dataset.theme = theme; try { localStorage.setItem('theme', theme) } catch {} }, [theme])

  const inp = mine
  const P = inp.protocol
  const ready = !!(P && inp.csv)
  const A = useMemo(() => (ready ? analyze(inp, vals) : null), [inp, vals, ready])
  const text = draft ?? (A ? A.summary : '')
  const issues = A ? A.checks.filter(c => c[0] !== 'ok').length : 0
  const toast = m => { setMsg(m); setTimeout(() => setMsg(''), 3000) }

  const generate = () => (ready ? setGen(true) : toast('Upload a protocol and a results CSV first.'))
  const loadSamples = () => { setMine(preset('complete')); setVals({}); setDraft(null); toast('Sample files loaded. You can replace any of them.') }
  const clear = () => { setMine(EMPTY); setVals({}); setDraft(null); setGen(false) }
  const onFile = async (kind, e) => {
    const fs = [...e.target.files]; e.target.value = ''
    if (!fs.length) return
    try {
      if (kind === 'protocol') { const protocol = parseProtocol(fs[0].name, await fs[0].text()); setMine(i => ({ ...i, protocol, protocolName: fs[0].name })) }
      if (kind === 'csv') { const csv = await fs[0].text(); setMine(i => ({ ...i, csv, csvName: fs[0].name })) }
      if (kind === 'docs') { const docs = await Promise.all(fs.map(async f => ({ name: f.name, text: await f.text() }))); setMine(i => ({ ...i, docs })) }
      if (kind === 'pdf') setMine(i => ({ ...i, pdf: { name: fs[0].name, url: URL.createObjectURL(fs[0]) } }))
      setVals({}); setDraft(null)
    } catch (err) { toast(err.message) }
  }
  const exportAs = async k => {
    try {
      save(await exportViaBackend(inp, vals, text, k), FILES[k])
      toast('Draft exported. Review before sharing.')
    } catch (e) { toast('Export failed: ' + e.message) }
  }
  const aiDraft = async () => {
    try {
      const r = await draftWithAi(inp, vals)
      setDraft(r.text)
      toast(r.source === 'ai' ? 'Drafted by Groq from verified results' : 'Used the rule-based draft: ' + r.source)
    } catch (e) { toast(e.message) }
  }
  const up = (kind, accept, label, multiple) => <Pick kind={kind} accept={accept} multiple={multiple} onFile={onFile}>{label}</Pick>

  return (<>
    <nav className="nav"><div className="in">
      <strong className="brand"><span className="mark" />Report Generator</strong>
      <div className="navr">
        <button className="tbtn" aria-label="Toggle dark mode" onClick={() => setTheme(t => (t === 'dark' ? 'light' : 'dark'))}><Icon d={theme === 'dark' ? I.sun : I.moon} s={17} /></button>
        <button className="pill" onClick={generate}>Generate report</button>
      </div>
    </div></nav>

    <main className="page">
      <header className="hero"><h1>Component inspection report</h1>
        <p>Upload your protocol, results and supporting files, then generate a draft report package.</p>
        {ready && <div className="chips"><span className="chip">{P.project}</span><span className="chip">{P.component}</span><span className="chip">{P.method}</span><span className="chip">{rng(P)}</span></div>}</header>
      <div className="grid">
        <div className="col">
          <section className="card tint-slate">
            <div className="sh"><h2>Input files</h2>
              <span className="acts"><button className="link" onClick={loadSamples}>Load sample files</button>{(mine.csv || mine.protocol) && <button className="link" onClick={clear}>Clear</button>}</span></div>
            <Row kind="json" badge="JSON" ok={!!P} title={inp.protocolName || 'Protocol'} meta={P ? `${P.project}, ${P.samples.length} planned samples, ${rng(P)}` : 'Required. JSON, or Markdown with key: value lines'}>{up('protocol', '.json,.md,.txt', P ? 'Replace' : 'Upload')}</Row>
            <Row kind="csv" badge="CSV" ok={!!inp.csv} title={inp.csvName || 'Results'} meta={inp.csv ? `${Math.max(0, inp.csv.trim().split(/\r?\n/).length - 1)} result rows` : 'Required. Columns: sample_id, measurement, unit, observation'}>{up('csv', '.csv', inp.csv ? 'Replace' : 'Upload')}</Row>
            <Row kind="md" badge="MD" ok={inp.docs.length > 0} title="Reference documents" meta={inp.docs.length ? inp.docs.map(d => d.name).join(', ') : 'Markdown files, not uploaded'}>{up('docs', '.md,.txt', inp.docs.length ? 'Replace' : 'Upload', true)}</Row>
            <Row kind="pdf" badge="PDF" ok={!!inp.pdf} title="Calibration record" meta={inp.pdf ? inp.pdf.name : 'Not supplied'}>{up('pdf', '.pdf', inp.pdf ? 'Replace' : 'Upload')}</Row>
          </section>

          {!ready ? <section className="card empty tint-blue"><h2>Add your files</h2><p>Upload a protocol and a results CSV to continue. Reference documents and the calibration PDF are optional, and anything missing is flagged in the checks.</p></section>
          : !gen ? <section className="card empty tint-blue"><h2>No report yet</h2><p>Select Generate report to build it from the files above.</p></section> : <>
            <div className="doc-head"><h2>Component inspection report</h2><span className="draft">Draft - Requires Review</span></div>

            <Section icon="doc" c="slate" title="Project information" tag="Template" source="Filled from the protocol. Fixed wording is locked.">
              <dl>
                <dt>Project ID</dt><dd>{P.project}</dd><dt>Component</dt><dd>{P.component}</dd>
                <dt>Test method</dt><dd>{P.method}</dd><dt>Acceptance range</dt><dd>{A.range}</dd>
                <dt>Calibration reference</dt><dd>{inp.pdf ? calText(P, inp.pdf) : <span className="unfilled">{'{{calibration_ref}}'} unfilled</span>}</dd>
              </dl>
              <p className="locked">{P.fixedStatement}</p>
            </Section>

            <Section icon="search" c="blue" title="Test method and guidance" tag="Retrieval" source={`Top passages from ${inp.docs.length} reference documents.`}>
              {A.passages.length ? A.passages.map(x => (
                <blockquote key={x.file + x.ref}>{x.text}<cite>{x.file}, {x.ref}</cite></blockquote>
              )) : <p className="warn-text">No reference passages found. Nothing was guessed.</p>}
            </Section>

            <Section icon="table" c="green" title="Results and evidence" tag="Calculated" source="Measurements from the CSV, judged against the protocol range. Full table in Attachment B.">
              <div className="scroll"><table>
                <thead><tr><th>Sample</th><th>Measurement ({P.unit})</th><th>Observation</th><th>Result</th></tr></thead>
                <tbody>
                  {A.rows.map(r => (<tr key={r.id} className={r.st}><td>{r.id}</td>
                    <td><input inputMode="decimal" aria-label={`Measurement for ${r.id}`} value={vals[r.id] ?? (r.value ?? '')} onChange={e => setVals(v => ({ ...v, [r.id]: e.target.value }))} /></td>
                    <td>{r.obs}</td><td><Badge st={r.st} /></td></tr>))}
                  {A.missing.map(id => (<tr key={id}><td>{id}</td><td colSpan="2" className="muted">No result supplied</td><td><Badge st="missing" /></td></tr>))}
                </tbody>
              </table></div>
              <p className="hint">Edit a measurement to see the result, summary, checks and Excel values update together.</p>
            </Section>

            <Section icon="spark" c="purple" title="Summary and observations" tag="AI drafted" source="Written from the verified results and retrieved context. Review and edit before export.">
              <textarea value={text} aria-label="Summary draft" onChange={e => setDraft(e.target.value)} />
              <div className="actions"><span className="muted">{draft === null ? 'In sync with results.' : 'Edited. Regenerate to match current results.'}</span>
                <span className="acts"><button className="link" onClick={aiDraft}>Draft with AI</button><button className="link" onClick={() => setDraft(null)}>Regenerate from results</button></span></div>
            </Section>
          </>}
        </div>

        <aside className="side">
          {!ready ? <section className="card tint-slate"><h2>Checks</h2><p className="muted">Validation appears once a protocol and a results file are added.</p></section> : <>
            <section className={'card ' + (issues ? 'tint-amber' : 'tint-green')}>
              <div className="sh"><h2>Validation</h2><span className={'tag ' + (issues ? 'amber' : 'green')}>{issues ? `${issues} to review` : 'All clear'}</span></div>
              {A.checks.map(([s, t, d]) => (<div className="chk" key={t}><span className={'dot ' + s} /><div><div>{t}</div><small className={s}>{d}</small></div></div>))}
            </section>
            <section className="card tint-purple">
              <h2>Attachments</h2>
              <Row kind="a" badge="A" title="Attachment A" ok={!!inp.pdf} meta={inp.pdf ? 'Calibration record' : 'Calibration record, missing'} />
              <Row kind="b" badge="B" title="Attachment B" meta="Results table, Results.xlsx" />
              <div className="kpi"><div className="k blue"><b>{A.total}</b>Total</div><div className="k green"><b>{A.passed}</b>Passed</div><div className={'k ' + (A.failed ? 'red' : 'grey')}><b>{A.failed}</b>Failed</div></div>
            </section>
            <section className="card tint-blue">
              <h2>Export</h2>
              <div className="btns">
                <button className="btn" disabled={!gen} onClick={() => exportAs('docx')}>Report (.docx)</button>
                <button className="btn" disabled={!gen} onClick={() => exportAs('xlsx')}>Results (.xlsx)</button>
                <button className="btn" disabled={!gen} onClick={() => exportAs('pdf')}>Combined PDF (.pdf)</button>
                <button className="btn primary" disabled={!gen} onClick={() => exportAs('zip')}>Download package (.zip)</button>
              </div>
            </section>
          </>}
        </aside>
      </div>
    </main>
    <div className={'toast' + (msg ? ' on' : '')} role="status">{msg}</div>
  </>)
}