import csv from './results.csv?raw'
import protocol from './protocol.json'
import calUrl from './calibration-record.pdf?url'
import r1 from './reference/REF-01-test-method.md?raw'
import r2 from './reference/REF-02-reporting-guidance.md?raw'
import r3 from './reference/REF-03-calibration-policy.md?raw'

const docs = [['REF-01-test-method.md', r1], ['REF-02-reporting-guidance.md', r2], ['REF-03-calibration-policy.md', r3]]
  .map(([name, text]) => ({ name, text }))

export const SCEN = { complete: 'Complete input', failing: 'Failing result', incomplete: 'Incomplete input' }

export function preset(k) {
  let c = csv
  if (k === 'failing') c = c.replace('S-003,2.45', 'S-003,2.71')
  if (k === 'incomplete') c = c.split('\n').filter(l => !l.startsWith('S-004')).join('\n')
  return { csv: c, csvName: 'results.csv', protocolName: 'protocol.json', docs, protocol,
    pdf: k === 'incomplete' ? null : { name: 'calibration-record.pdf', url: calUrl } }
}
