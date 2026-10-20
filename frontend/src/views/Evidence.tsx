/* eslint-disable @typescript-eslint/no-explicit-any */
import { CheckCircle2, Download, Package, ShieldCheck, XCircle } from 'lucide-react'
import { useState } from 'react'
import { api, fmtBytes, fmtTime, vaultUrl } from '../api'
import { PageHeader, Section, Spinner } from '../components/ui'
import { useCase } from '../context'
import { useApi } from '../hooks'

const KIND_LABEL: Record<string, string> = {
  original: 'Original (uploaded)', download: 'Discovered media', capture_screenshot: 'Page screenshot', capture_html: 'Page HTML',
  capture_mhtml: 'Page snapshot (MHTML)', capture_headers: 'HTTP headers & metadata', artifact: 'Analysis artifact',
}

export default function Evidence() {
  const { caseId, tick, toast } = useCase()
  const { data, reload } = useApi<any>(`/api/cases/${caseId}/evidence`, [tick])
  const [verify, setVerify] = useState<any>(null)
  const [filter, setFilter] = useState('evidence')
  if (!data) return <Spinner />
  const recs = data.records.filter((r: any) => filter === 'all' || r.kind !== 'artifact')
  return (
    <div>
      <PageHeader step="Preserve" title="Evidence package"
        question="Is everything preserved, hashed and accounted for before it disappears?"
        right={<button className="btn btn-primary" onClick={async () => { await api.post(`/api/cases/${caseId}/export`); toast('Building evidence package (ZIP + PDF case file)…') }}><Package size={14} /> Export forensic package</button>} />
      <div className="grid gap-5 lg:grid-cols-3">
        <Stat label="Preserved items" value={data.records.filter((r: any) => r.kind !== 'artifact').length} sub={`${data.records.length} incl. analysis artifacts`} />
        <div className="card p-5">
          <div className="label">Custody chain</div>
          <div className="mt-2 flex items-center gap-2 text-lg font-semibold">
            {data.custody.chain_ok ? <CheckCircle2 className="text-good" size={20} /> : <XCircle className="text-crit" size={20} />}
            {data.custody.chain_ok ? 'Intact' : 'BROKEN'} <span className="text-sm font-normal text-ink-3">· {data.custody.count} entries</span>
          </div>
          {data.custody.problems.map((p: string) => <div key={p} className="text-xs text-crit">{p}</div>)}
          <div className="mt-1 text-[12px] text-ink-3">Hash-chained. Editing, inserting or deleting any entry breaks verification.</div>
        </div>
        <div className="card p-5">
          <div className="label">File integrity</div>
          {verify ? <div className="mt-2 text-lg font-semibold">{verify.failures.length === 0 ? <span className="flex items-center gap-2"><ShieldCheck className="text-good" size={20} /> {verify.items} files match</span> : <span className="text-crit">{verify.failures.length} mismatches</span>}</div>
            : <button className="btn mt-2" onClick={async () => setVerify(await api.post(`/api/cases/${caseId}/evidence/verify`))}>Re-hash every vault file</button>}
          <div className="mt-1 text-[12px] text-ink-3">Vault files are write-once and named by their SHA-256.</div>
        </div>
      </div>

      {data.exports.length > 0 && (
        <Section className="mt-5" title="Exported packages" subtitle="Each ZIP contains originals, captures, the manifest, hashes.csv, the custody log, the PDF case file, the complaint draft, a draft BSA s.63 certificate and verify_package.py.">
          <div className="space-y-1.5">{data.exports.map((e: any) => (
            <a key={e.name} href={`/api/cases/${caseId}/exports/${e.name}`} className="flex items-center gap-3 rounded-lg border border-line px-3 py-2 text-[13px] hover:border-line-strong">
              <Download size={14} /> <span className="mono">{e.name}</span><span className="ml-auto text-ink-3">{fmtBytes(e.size)}</span></a>
          ))}</div>
        </Section>
      )}

      <Section className="mt-5" title="Evidence register" right={
        <div className="w-44"><select className="input" value={filter} onChange={(e) => setFilter(e.target.value)}><option value="evidence">Evidence only</option><option value="all">Include artifacts</option></select></div>}>
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead><tr className="border-b border-line text-left text-ink-3"><th className="py-2 pr-3 font-medium">Item</th><th className="pr-3 font-medium">Kind</th><th className="pr-3 font-medium">SHA-256 / MD5</th><th className="pr-3 font-medium">Acquired</th><th className="font-medium">Source</th></tr></thead>
            <tbody>{recs.map((r: any) => (
              <tr key={r.sha256} className="border-b border-line/60 align-top">
                <td className="py-2 pr-3">{vaultUrl(caseId, r.sha256, true) ? <a className="underline decoration-dotted" href={vaultUrl(caseId, r.sha256, true)}>{r.filename}</a> : <span title="Not kept in the saved demo">{r.filename}</span>}<div className="text-ink-3">{fmtBytes(r.size)}</div></td>
                <td className="pr-3 text-ink-2">{KIND_LABEL[r.kind] ?? r.kind}</td>
                <td className="mono pr-3 text-[10.5px] text-ink-2">{r.sha256}<div className="text-ink-3">{r.md5}</div></td>
                <td className="whitespace-nowrap pr-3 text-ink-2">{fmtTime(r.stored_at?.utc)}</td>
                <td className="max-w-xs break-all text-ink-3">{r.acquisition?.page_url || r.acquisition?.requested_url || r.acquisition?.method || '—'}{r.acquisition?.tool && <div>{r.acquisition.tool}</div>}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </Section>

      <Section className="mt-5" title="Chain of custody" subtitle="Latest entries first." right={<button className="btn" onClick={reload}>Refresh</button>}>
        <div className="max-h-[420px] overflow-y-auto">
          <table className="w-full text-[11.5px]">
            <tbody>{[...data.custody.entries].reverse().map((e: any) => (
              <tr key={e.seq} className="border-b border-line/50 align-top">
                <td className="mono w-10 py-1.5 pr-2 text-ink-3">{e.seq}</td>
                <td className="mono whitespace-nowrap pr-3 text-ink-3">{fmtTime(e.time.utc, false)}</td>
                <td className="pr-3 font-medium">{e.action}</td>
                <td className="pr-3 text-ink-2">{e.actor}</td>
                <td className="text-ink-3">{e.tool}{e.item_sha256 && <span className="mono"> · {e.item_sha256.slice(0, 12)}…</span>}{e.details?.url && <span className="break-all"> · {e.details.url}</span>}</td>
                <td className="mono pl-2 text-[10px] text-ink-3" title={e.entry_hash}>{e.entry_hash.slice(0, 8)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </Section>
    </div>
  )
}

function Stat({ label, value, sub }: { label: string; value: number | string; sub?: string }) {
  return <div className="card p-5"><div className="label">{label}</div><div className="mt-1 text-3xl font-semibold">{value}</div>{sub && <div className="text-[12px] text-ink-3">{sub}</div>}</div>
}
