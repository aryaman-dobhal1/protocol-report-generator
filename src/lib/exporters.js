import * as XLSX from 'xlsx'
import JSZip from 'jszip'
import { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, WidthType, HeadingLevel } from 'docx'
import { fmt, calText } from './engine'

export const save = (blob, name) => {
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob); a.download = name; a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 1000)
}

export function buildXlsx({ inp, A }) {
  const P = inp.protocol
  const body = A.rows.map((r, i) => {
    const n = i + 2
    const res = r.value == null ? { t: 's', v: 'Missing' }
      : { t: 's', v: r.label, f: `IF(AND(B${n}>=E${n},B${n}<=F${n}),"Pass","Fail")` }
    return [r.id, r.value, r.unit, r.obs, P.min, P.max, res]
  })
  const ws = XLSX.utils.aoa_to_sheet([['Sample ID', 'Measurement', 'Unit', 'Observation', 'Min', 'Max', 'Result'], ...body,
    [], ['Total samples', A.total], ['Passed', A.passed], ['Failed', A.failed]])
  ws['!cols'] = [12, 13, 7, 22, 8, 8, 10].map(wch => ({ wch }))
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, 'Results')
  return new Blob([XLSX.write(wb, { type: 'array', bookType: 'xlsx' })])
}

const para = (t, o = {}) => new Paragraph({ children: [new TextRun({ text: t, ...o })], spacing: { after: 120 } })
const head = t => new Paragraph({ text: t, heading: HeadingLevel.HEADING_2, spacing: { before: 240, after: 120 } })
const cell = t => new TableCell({ children: [para(String(t))] })

export function buildDocx({ inp, A, text }) {
  const p = inp.protocol
  const info = [['Project ID', p.project], ['Component', p.component], ['Test method', p.method],
    ['Acceptance range', A.range], ['Planned samples', String(p.samples.length)],
    ['Calibration reference', calText(p, inp.pdf) ?? '{{calibration_ref}} unfilled']]
  const rows = [['Sample', 'Measurement', 'Unit', 'Observation', 'Result'],
    ...A.rows.map(r => [r.id, fmt(r.value), r.unit, r.obs, r.label]),
    ...A.missing.map(id => [id, '-', '-', 'No result supplied', 'Missing'])]
  const children = [
    new Paragraph({ text: 'Component Inspection Report', heading: HeadingLevel.TITLE }),
    para('Draft - Requires Review', { bold: true, color: 'B25000' }),
    head('1. Project information'), ...info.map(([k, v]) => para(`${k}: ${v}`)), para(p.fixedStatement, { italics: true }),
    head('2. Test method and guidance'), ...A.passages.map(x => para(`${x.text} [${x.file}, ${x.ref}]`)),
    head('3. Results and evidence'),
    new Table({ width: { size: 100, type: WidthType.PERCENTAGE }, rows: rows.map(r => new TableRow({ children: r.map(cell) })) }),
    para('Full results table: Attachment B (Results.xlsx). ' + (inp.pdf ? 'Calibration record: Attachment A.' : 'Attachment A (calibration record) was not supplied.')),
    head('4. Summary and observations'), ...text.split('\n').map(t => para(t))]
  return Packer.toBlob(new Document({ sections: [{ children }] }))
}

export async function buildZip(ctx) {
  const zip = new JSZip()
  zip.file('Report.docx', await buildDocx(ctx))
  zip.file('Attachment B - Results.xlsx', buildXlsx(ctx))
  if (ctx.inp.pdf) zip.file('Attachment A - ' + ctx.inp.pdf.name, await (await fetch(ctx.inp.pdf.url)).arrayBuffer())
  return zip.generateAsync({ type: 'blob' })
}
