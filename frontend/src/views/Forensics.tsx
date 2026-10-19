/* eslint-disable @typescript-eslint/no-explicit-any */
import { ChevronDown, ChevronRight, CircleDashed, Play, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, vaultUrl } from '../api'
import { Empty, LoadError, PageHeader, Section, Spinner } from '../components/ui'
import { useCase } from '../context'
import { useApi } from '../hooks'

const KIND: Record<string, { label: string; desc: string; color: string }> = {
  observation: { label: 'Measured', desc: 'A direct, reproducible measurement of the file. No interpretation.', color: 'var(--color-st-verified)' },
  supported: { label: 'Supported', desc: 'An interpretation whose error rates were measured against ground truth.', color: 'var(--color-st-corroborated)' },
  hypothesis: { label: 'Hypothesis', desc: 'An interpretation that has not been validated. A lead, not a conclusion.', color: 'var(--color-st-lead)' },
  unknown: { label: 'Unknown', desc: 'Could not be assessed. Missing evidence is never read as clean.', color: 'var(--color-st-unknown)' },
}
const TIER: Record<string, string> = {
  real: 'Validated on real labelled data', synthetic: 'Validated on simulated data only', none: 'Not validated against ground truth',
}

function KindTag({ kind }: { kind: string }) {
  const k = KIND[kind] ?? KIND.unknown
  return <span className="tag shrink-0" title={k.desc} style={{ borderColor: k.color, borderStyle: kind === 'hypothesis' ? 'dotted' : kind === 'unknown' ? 'dashed' : 'solid' }}>{k.label}</span>
}

function Finding({ f, ev, caseId }: { f: any; ev: any[]; caseId: string }) {
  const [open, setOpen] = useState(false)
  const imgs = (f.evidence ?? []).map((id: string) => ev.find((e) => e.id === id)).filter((e: any) => e?.kind === 'image' && e.sha256)
  const hasMore = f.why || f.caution || imgs.length || (f.data && Object.keys(f.data).length)
  return (
    <li className="py-2.5">
      <div className="flex items-start gap-2.5">
        <KindTag kind={f.kind} />
        <div className="min-w-0 flex-1">
          <div className="text-[13.5px] leading-snug text-ink">{f.statement}</div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-3 text-[11.5px] text-ink-3">
            <span>{f.title}</span>
            {f.confidence != null && <span>confidence {Math.round(f.confidence * 100)}% ({f.confidence_label ?? ''})</span>}
            {hasMore && (
              <button className="flex items-center gap-0.5 text-ink-2 underline decoration-dotted" aria-expanded={open} onClick={() => setOpen(!open)}>
                {open ? <ChevronDown size={12} aria-hidden /> : <ChevronRight size={12} aria-hidden />} details
              </button>
            )}
          </div>
          {open && (
            <div className="mt-2 space-y-2 border-l-2 border-line pl-3 text-[12.5px] text-ink-2">
              {f.why && <p><span className="label mr-2">Meaning</span>{f.why}</p>}
              {f.caution && <p><span className="label mr-2">Caution</span>{f.caution}</p>}
              {imgs.map((e: any) => (
                <a key={e.id} href={vaultUrl(caseId, e.sha256)} target="_blank" rel="noreferrer" className="block w-fit">
                  <img src={vaultUrl(caseId, e.sha256)} alt={e.label} className="max-h-64 rounded border border-line" />
                  <span className="text-[11px] text-ink-3">{e.label}</span>
                </a>
              ))}
              {f.data && Object.keys(f.data).length > 0 && (
                <pre className="mono max-h-40 overflow-auto rounded bg-bg p-2 text-[11px] text-ink-3">{JSON.stringify(f.data, null, 1)}</pre>
              )}
            </div>
          )}
        </div>
      </div>
    </li>
  )
}

function ModuleCard({ r, caseId, busy, onRun }: { r: any; caseId: string; busy: boolean; onRun: () => void }) {
  const [open, setOpen] = useState(r.status === 'ok' && r.findings.some((f: any) => f.kind === 'supported' || f.kind === 'hypothesis'))
  const counts = (k: string) => r.findings.filter((f: any) => f.kind === k).length
  const conf = r.confidence
  return (
    <section className="card">
      <div className="flex items-start gap-3 p-4">
        <button className="mt-0.5 text-ink-3" aria-expanded={open} aria-label={`${open ? 'Collapse' : 'Expand'} ${r.title}`} onClick={() => setOpen(!open)}>
          {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="display text-[16px] font-semibold">{r.title}</h3>
            <span className="mono text-[11px] text-ink-3">v{r.version}</span>
            {r.status !== 'ok' && <span className="tag">{r.status.replace('_', ' ')}</span>}
          </div>
          <p className="mt-1 text-[13px] text-ink-2">{r.summary}</p>
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-ink-3">
            {(['observation', 'supported', 'hypothesis', 'unknown'] as const).map((k) => counts(k) > 0 && <span key={k} className="flex items-center gap-1.5"><KindTag kind={k} /> {counts(k)}</span>)}
            <span>{TIER[r.validation?.tier ?? 'none']}</span>
            <span>{r.runtime_ms} ms</span>
          </div>
        </div>
        <div className="w-44 shrink-0 text-right">
          <div className="label">Confidence</div>
          {conf != null ? (
            <>
              <div className="meter mt-1.5" role="img" aria-label={`Confidence ${Math.round(conf * 100)} percent, ${r.confidence_label}`}><i style={{ width: `${conf * 100}%` }} /></div>
              <div className="mt-1 text-[12px] text-ink-2">{Math.round(conf * 100)}% · {r.confidence_label}</div>
            </>
          ) : <div className="mt-1 text-[12px] text-ink-3">none: nothing interpretive was concluded</div>}
        </div>
        <button className="btn btn-quiet !min-h-0 !p-1.5" disabled={busy} onClick={onRun} title="Re-run this module"><Play size={14} /><span className="sr-only">Re-run {r.title}</span></button>
      </div>
      {open && (
        <div className="border-t border-line px-4 pb-4 pt-2">
          {r.error && <div className="mb-2 rounded border border-crit/60 bg-crit/5 px-3 py-2 text-[12.5px]">The module failed: {r.error}</div>}
          <ul className="divide-y divide-line">{r.findings.map((f: any) => <Finding key={f.id} f={f} ev={r.evidence ?? []} caseId={caseId} />)}</ul>
          {r.confidence_basis && <p className="mt-3 text-[12px] text-ink-3"><b className="text-ink-2">Confidence basis. </b>{r.confidence_basis}</p>}
          <details className="mt-3 text-[12.5px] text-ink-3">
            <summary className="cursor-pointer text-ink-2">Limitations, versions and validation</summary>
            <div className="mt-2 grid gap-4 md:grid-cols-2">
              <div><div className="label mb-1">What this cannot tell you</div><ul className="list-disc space-y-1 pl-5">{r.limitations.map((l: string) => <li key={l}>{l}</li>)}</ul></div>
              <div>
                <div className="label mb-1">Tools and versions</div>
                <ul className="space-y-0.5">{Object.entries(r.versions ?? {}).map(([k, v]) => <li key={k} className="mono text-[11px]">{k}: {String(v).slice(0, 80)}</li>)}</ul>
                <div className="label mb-1 mt-3">Validation</div>
                <p>{r.validation?.summary}</p>
                {r.validation?.dataset && <p className="mt-1">Data: {r.validation.dataset}</p>}
              </div>
            </div>
            {r.result_sha256 && <p className="mono mt-3 text-[11px]">Result stored in the evidence vault: sha256:{r.result_sha256.slice(0, 20)}…</p>}
          </details>
        </div>
      )}
    </section>
  )
}

function Devices({ caseId, images, devices, onChange }: { caseId: string; images: any[]; devices: any[]; onChange: () => void }) {
  const { toast } = useCase()
  const [name, setName] = useState('')
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const bySize = new Map<string, any[]>()
  images.forEach((m) => { const k = `${m.width}×${m.height}`; bySize.set(k, [...(bySize.get(k) ?? []), m]) })
  const pickSize = (k: string) => setSel(new Set((bySize.get(k) ?? []).map((m) => m.id)))
  const create = async () => {
    setBusy(true)
    try {
      await api.post(`/api/cases/${caseId}/devices`, { name, reference_media_ids: [...sel] })
      toast('Building the fingerprint: this can take a minute')
      setName(''); setSel(new Set())
      setTimeout(onChange, 1500)
    } catch (e) { toast((e as Error).message, 'err') } finally { setBusy(false) }
  }
  return (
    <Section title="Candidate devices" subtitle="A fingerprint built from photos you state came from ONE suspected camera. The sensor-noise module then tests each image against it. A match supports, never proves, a shared sensor.">
      {devices.length > 0 && (
        <ul className="mb-4 divide-y divide-line">
          {devices.map((d) => (
            <li key={d.id} className="flex items-start gap-3 py-2.5 text-[13px]">
              <div className="min-w-0 flex-1">
                <div className="font-semibold">{d.name} <span className="mono text-[11px] font-normal text-ink-3">{d.width}×{d.height} · {d.n_references} photos</span></div>
                <div className="text-[12px] text-ink-3">{d.coherent ? 'Every reference photo matches the pattern built from the others.' : `Warning: ${d.suspect_references?.length ? `${d.suspect_references.join(', ')} did not match the others (another camera, or too few or too small photos for a reliable check).` : 'not every reference photo matches the others.'} A fingerprint built from mixed sources is unreliable; its matches are down-weighted.`}</div>
                <div className="mono text-[11px] text-ink-3">fingerprint sha256:{d.fingerprint_sha256.slice(0, 16)}…</div>
              </div>
              <button className="btn btn-quiet btn-danger !min-h-0 !p-1.5" title="Remove from the working list" onClick={async () => { await api.delete(`/api/cases/${caseId}/devices/${d.id}`); onChange() }}><Trash2 size={14} /><span className="sr-only">Remove {d.name}</span></button>
            </li>
          ))}
        </ul>
      )}
      {images.length < 5 ? (
        <p className="text-[13px] text-ink-3">Add at least 5 photos from the suspected camera (use “Add authentic reference” on the left), all at the same pixel size, then build a fingerprint here.</p>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-ink-3">Select photos by size:
            {[...bySize.entries()].filter(([, v]) => v.length >= 5).map(([k, v]) => <button key={k} className="btn !min-h-0 !px-2 !py-0.5 text-[12px]" onClick={() => pickSize(k)}>{k} ({v.length})</button>)}
          </div>
          <ul className="grid max-h-44 gap-1 overflow-y-auto sm:grid-cols-2">
            {images.map((m) => (
              <li key={m.id}><label className="flex items-center gap-2 text-[12.5px]"><input type="checkbox" checked={sel.has(m.id)} onChange={(e) => { const n = new Set(sel); e.target.checked ? n.add(m.id) : n.delete(m.id); setSel(n) }} /> <span className="truncate">{m.label}</span> <span className="mono text-[11px] text-ink-3">{m.width}×{m.height}</span></label></li>
            ))}
          </ul>
          <div className="flex items-center gap-2">
            <label className="sr-only" htmlFor="dev-name">Device name</label>
            <input id="dev-name" className="input max-w-xs" placeholder="Name, e.g. Suspect phone A" value={name} onChange={(e) => setName(e.target.value)} />
            <button className="btn btn-primary" disabled={busy || sel.size < 5 || !name.trim()} onClick={create}>Build fingerprint ({sel.size} photos)</button>
          </div>
          <p className="text-[12px] text-ink-3">You are attesting that these photos came from one camera. The attestation, the photos' hashes and the fingerprint's hash are written to the chain of custody. Do not include photos that show the same picture as the questioned image.</p>
        </div>
      )}
    </Section>
  )
}

export default function Forensics() {
  const { caseId, data, tick, refresh, toast } = useCase()
  const { data: fx, error, reload } = useApi<any>(`/api/cases/${caseId}/forensics`, [tick])
  const [mid, setMid] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (!mid && fx?.media?.length) setMid((fx.media.find((m: any) => m.role === 'primary') ?? fx.media[0]).media_id) }, [fx, mid])
  if (error) return <LoadError what="forensic modules" error={error} onRetry={reload} />
  if (!fx) return <Spinner label="Loading forensic modules" />
  const cur = fx.media.find((m: any) => m.media_id === mid)
  const images = data.media.filter((m) => m.modality === 'image')
  const run = async (modules?: string[]) => {
    if (!cur) return
    setBusy(true)
    try {
      await api.post(`/api/cases/${caseId}/forensics/run`, { media_id: cur.media_id, modules })
      toast('Running forensic modules')
      setTimeout(() => { refresh(); setBusy(false) }, 2500)
    } catch (e) { toast((e as Error).message, 'err'); setBusy(false) }
  }
  return (
    <div className="max-w-5xl space-y-5">
      <PageHeader step="Investigate deeper" title="Forensic modules"
        question="Six independent checks of how a file was captured, processed and edited. Each says what it measured, how far it can be trusted, and what it cannot tell you. Findings sit beside the verdict and do not change it." />
      {fx.media.length === 0 ? <Empty>No media in this case yet.</Empty> : (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <label htmlFor="fm-media" className="label">File</label>
            <select id="fm-media" className="input !w-auto min-w-64" value={mid ?? ''} onChange={(e) => setMid(e.target.value)}>
              {fx.media.map((m: any) => <option key={m.media_id} value={m.media_id}>{m.label} ({m.modality}{m.role !== 'primary' ? `, ${m.role}` : ''})</option>)}
            </select>
            <button className="btn" disabled={busy} onClick={() => run()}><Play size={14} aria-hidden /> {cur?.results?.length ? 'Re-run all modules' : 'Run modules'}</button>
          </div>
          {cur && cur.results.length === 0 && (
            <div className="card flex items-center gap-3 p-5 text-[13px] text-ink-2"><CircleDashed size={16} aria-hidden /> No module results for this file yet. They are produced during analysis; press “Run modules” to produce them now.</div>
          )}
          <div className="space-y-3">{cur?.results.map((r: any) => <ModuleCard key={r.module} r={r} caseId={caseId} busy={busy} onRun={() => run([r.module])} />)}</div>
        </>
      )}
      <Devices caseId={caseId} images={images} devices={fx.devices} onChange={() => { refresh(); reload() }} />
    </div>
  )
}
