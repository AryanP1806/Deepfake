/* eslint-disable @typescript-eslint/no-explicit-any */
import { Camera, FlaskConical, TriangleAlert } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api, fmtTime } from '../api'
import { Empty, PageHeader, Pill, Section, SensitiveImg, Spinner } from '../components/ui'
import { useCase } from '../context'
import { useApi } from '../hooks'

const SEV: Record<string, { tone: 'crit' | 'warn' | 'good' | 'neutral'; label: string }> = {
  high: { tone: 'crit', label: 'Strong warning' }, warn: { tone: 'warn', label: 'Warning' }, info: { tone: 'neutral', label: 'Note' }, ok: { tone: 'good', label: 'OK' },
}

export default function Source() {
  const { caseId, tick, data: cd, toast } = useCase()
  const { data, error, reload } = useApi<any>(`/api/cases/${caseId}/source`, [tick])
  const [sel, setSel] = useState<string | null>(null)
  if (error && !data) return <div className="card max-w-2xl border-crit/60 p-5 text-sm">Could not load the source report: {error}</div>
  if (!data) return <Spinner label="Reading the file's metadata" />
  const list: any[] = data.media
  if (!list.length) return <div className="max-w-3xl"><PageHeader step="Investigate deeper" title="Device & source" question="Which camera or software does the file say it came from?" /><Empty>No analysed media yet.</Empty></div>
  const m = list.find((x) => x.media_id === sel) ?? list[0]
  const thumb = cd.media.find((x) => x.id === m.media_id)?.thumb
  const d = m.device

  return (
    <div className="max-w-5xl">
      <PageHeader step="Investigate deeper" title="Device & source"
        question="Which camera, phone or software does the file say it came from, which timestamps survive, and how much of that can be trusted?" />
      {list.length > 1 && <div className="mb-4 flex flex-wrap gap-2">{list.map((x: any) => (
        <button key={x.media_id} className={`btn px-3 py-1.5 text-[12px] ${x.media_id === m.media_id ? 'border-ink' : ''}`} onClick={() => setSel(x.media_id)}>{x.label.slice(0, 30)}</button>))}</div>}

      <div className="card mb-4 flex gap-5 p-5">
        <SensitiveImg caseId={caseId} sha={thumb} blur={cd.blur} className="h-24 w-24 shrink-0 rounded bg-black" />
        <div className="min-w-0 flex-1">
          <div className="label flex items-center gap-1.5"><Camera size={12} /> What the file says</div>
          <div className="mt-1 text-[20px] font-semibold leading-snug">{d.statement}</div>
          {d.claimed && <div className="mt-1.5 flex flex-wrap gap-2 text-[12.5px]">
            {Object.entries(d.claimed).map(([k, v]) => <span key={k} className="rounded border border-line px-2 py-0.5"><span className="text-ink-3">{k}</span> {String(v)}</span>)}
            <Pill tone="warn">{d.tier}</Pill></div>}
          <p className="mt-2 text-[13px] text-ink-2">{d.note}</p>
          <p className="mt-2 text-[12.5px] text-ink-3">{m.summary}</p>
        </div>
      </div>

      {m.consistency.length > 0 && (
        <Section title="Consistency checks" subtitle="Things that do or don't fit together inside the file. A warning is a reason to look closer, not proof of manipulation.">
          <ul className="space-y-2">{m.consistency.map((f: any) => (
            <li key={f.id} className="flex gap-3 text-[13px]"><span className="w-28 shrink-0"><Pill tone={SEV[f.severity]?.tone}>{SEV[f.severity]?.label}</Pill></span><span className="text-ink-2">{f.text}</span></li>))}</ul>
        </Section>
      )}

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Section title="Dates and times in the file" subtitle="All declared by the file itself: editable, and often without a time zone.">
          {m.timestamps.length === 0 ? <div className="text-[13px] text-ink-3">The file carries no timestamps.</div> : (
            <table className="w-full text-[12.5px]"><tbody>{m.timestamps.map((t: any, i: number) => (
              <tr key={i} className="border-b border-line/60 align-top"><td className="py-1.5 pr-3 text-ink-2">{t.label}</td><td className="mono pr-3 text-[12px]">{t.value}</td><td className="text-[11px] text-ink-3">tz: {t.timezone}</td></tr>))}</tbody></table>
          )}
          {m.outside_the_file.length > 0 && <>
            <div className="label mb-1 mt-4">Outside the file (for comparison)</div>
            <ul className="space-y-1 text-[12px] text-ink-2">{m.outside_the_file.map((o: any, i: number) => <li key={i}>{o.label}: <span className="mono text-[11.5px]">{fmtTime(o.value)}</span> <span className="text-ink-3">({o.source})</span></li>)}</ul></>}
        </Section>
        <Section title="Software and editing history">
          {m.software.length === 0 && m.edit_history.length === 0 ? <div className="text-[13px] text-ink-3">No software or editing history is recorded.</div> : (
            <ul className="space-y-1.5 text-[12.5px]">
              {m.software.map((x: any, i: number) => <li key={i}><Pill tone={x.kind === 'generator' ? 'crit' : x.kind === 'editing' ? 'warn' : 'neutral'}>{x.kind}</Pill> <span className="text-ink-2">{x.field}: {x.value}</span></li>)}
              {m.edit_history.map((x: any, i: number) => <li key={`h${i}`} className="text-ink-2"><span className="text-ink-3">history</span> {x.action ?? 'step'} — {x.agent}</li>)}
            </ul>)}
          {m.identity_fields.length > 0 && <><div className="label mb-1 mt-4">Owner / author fields (declared, editable)</div>
            <ul className="space-y-1 text-[12px] text-ink-2">{m.identity_fields.map((x: any, i: number) => <li key={i}><span className="text-ink-3">{x.field}</span> {x.value}</li>)}</ul></>}
        </Section>
      </div>

      <Section className="mt-4" title="What survived, and what did not" subtitle="Social platforms remove most metadata, so absence is normal.">
        <div className="grid gap-x-8 gap-y-1.5 text-[12.5px] md:grid-cols-2">
          {m.inventory.map((x: any) => <div key={x.group} className="flex justify-between gap-3 border-b border-line/50 py-1"><span className="text-ink-2">{x.group}</span>
            <span className={x.present ? 'text-good' : 'text-ink-3'}>{x.present ? `✓ ${x.detail}` : 'not in file'}</span></div>)}
        </div>
        {m.platform_hints?.length > 0 && <div className="mt-3 rounded-lg border border-line p-3 text-[12.5px] text-ink-2"><b>Looks like it was processed by a social platform:</b>{' '}
          {m.platform_hints.map((h: any) => `${h.label} (${h.why})`).join('; ')}. <span className="text-ink-3">A hint only — many other files share these sizes.</span></div>}
      </Section>

      <Prnu data={data} m={m} caseId={caseId} toast={toast} reload={reload} />

      <div className="mt-4 rounded-lg border border-line p-4 text-[12px] text-ink-3">
        <div className="mb-1 flex items-center gap-1.5 font-semibold text-ink-2"><TriangleAlert size={13} /> Limits</div>
        <ul className="list-disc space-y-0.5 pl-4">{m.limits.map((x: string) => <li key={x}>{x}</li>)}</ul>
      </div>
    </div>
  )
}

function Prnu({ data, m, caseId, toast, reload }: { data: any; m: any; caseId: string; toast: (s: string, t?: 'ok' | 'err') => void; reload: () => void }) {
  const refs: any[] = data.reference_images
  const sameSize = refs.filter((r) => r.width === m.image?.size?.[0] && r.height === m.image?.size?.[1])
  const [picked, setPicked] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const timer = useRef<number>(0)
  useEffect(() => () => clearTimeout(timer.current), [])
  const res: any[] = m.prnu ?? []
  const others = data.media.filter((x: any) => x.media_id !== m.media_id && x.modality === 'image')

  const poll = (n = 0) => { timer.current = window.setTimeout(async () => { await reload(); if (n < 40) poll(n + 1) }, 3000) }
  const run = async (body: any) => {
    setBusy(true)
    try { await api.post(`/api/cases/${caseId}/prnu`, { target_media_id: m.media_id, ...body }); toast('Comparing sensor noise… this takes a few seconds'); poll() }
    catch (e) { toast((e as Error).message, 'err') } finally { setBusy(false) }
  }
  const req = data.prnu_requirements
  return (
    <Section className="mt-4" title={<span className="flex items-center gap-2"><FlaskConical size={15} /> Camera sensor fingerprint (PRNU) — advanced research feature</span>}
      subtitle="Every camera sensor leaves a faint, fixed noise pattern. Photos known to come from a suspected camera can be tested against this file.">
      <div className="rounded-lg border border-warn/40 bg-warn/5 p-3 text-[12.5px] text-ink-2">
        <b className="text-ink">Read this first.</b> It never says which camera made a file; it only tests a <i>specific suspected camera</i>. It needs at least {req.min_references} reference photos taken with
        that exact device, all with <b>exactly the same pixel size</b> as this image. Resizing, cropping, heavy compression and WhatsApp/Instagram processing destroy it, and AI-generated
        images have no sensor pattern. A phone <i>model</i> is not a device. Validated on synthetic sensors only, not on a camera test set.
      </div>
      {m.modality !== 'image' ? <div className="mt-3 text-[13px] text-ink-3">Sensor-noise comparison works on still images.</div> : (
        <div className="mt-4 grid gap-5 lg:grid-cols-2">
          <div>
            <div className="label mb-1.5">Compare with a suspected camera</div>
            {refs.length === 0 ? <div className="text-[12.5px] text-ink-3">No reference photos yet. Use <b>Add authentic reference</b> (bottom left) to upload photos taken with the suspected camera.</div> : (<>
              <div className="mb-1 text-[11.5px] text-ink-3">{sameSize.length} of {refs.length} reference photo(s) match this image's size ({m.image?.size?.[0]}×{m.image?.size?.[1]}).</div>
              <ul className="max-h-40 space-y-1 overflow-y-auto text-[12.5px]">{refs.map((r) => {
                const ok = sameSize.some((s) => s.id === r.id)
                return <li key={r.id}><label className={`flex items-center gap-2 ${ok ? '' : 'opacity-50'}`}><input type="checkbox" disabled={!ok} checked={picked.includes(r.id)}
                  onChange={(e) => setPicked(e.target.checked ? [...picked, r.id] : picked.filter((x) => x !== r.id))} />{r.label} <span className="text-ink-3">{r.width}×{r.height}</span></label></li>
              })}</ul>
              <div className="mt-2 flex gap-2"><button className="btn" onClick={() => setPicked(sameSize.map((r) => r.id))}>Select all that fit</button>
                <button className="btn btn-primary" disabled={busy || picked.length < req.min_references} onClick={() => run({ reference_media_ids: picked })}>Compare ({picked.length})</button></div></>)}
            {others.length > 0 && <div className="mt-4"><div className="label mb-1.5">…or: do this file and another file here share a sensor?</div>
              <select className="input" defaultValue="" onChange={(e) => e.target.value && run({ compare_with: e.target.value })}>
                <option value="">Choose another file…</option>{others.map((o: any) => <option key={o.media_id} value={o.media_id}>{o.label.slice(0, 40)}</option>)}</select></div>}
          </div>
          <div>
            <div className="label mb-1.5">Results</div>
            {res.length === 0 ? <div className="text-[12.5px] text-ink-3">No comparison run yet.</div> : res.map((r: any, i: number) => (
              <div key={i} className="mb-2 rounded-lg border border-line p-3 text-[12.5px]">
                <div className="flex items-center justify-between"><b>{r.kind === 'pair' ? 'Compared with another file in this case' : `Against ${r.references?.length ?? 0} reference photo(s)`}</b>
                  <Pill tone={r.status === 'consistent' ? 'warn' : r.status === 'not_comparable' ? 'neutral' : 'neutral'}>{({ consistent: 'Consistent with same sensor', not_consistent: 'Not consistent', inconclusive: 'Inconclusive', not_comparable: 'Cannot be compared' } as any)[r.status]}</Pill></div>
                <p className="mt-1 text-ink-2">{r.summary}</p>
                {r.pce != null && <div className="mono mt-1 text-[11.5px] text-ink-3">PCE {r.pce} (match ≥ {r.thresholds?.match}; below {r.thresholds?.noise} = chance) · shift {JSON.stringify(r.shift)}</div>}
              </div>))}
          </div>
        </div>)}
    </Section>
  )
}
