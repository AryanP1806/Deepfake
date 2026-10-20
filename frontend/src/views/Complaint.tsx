/* eslint-disable @typescript-eslint/no-explicit-any */
import { guardDemo } from '../lib/demo'
import { Check, Copy, Download, ExternalLink, FileText, Phone, RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmtBytes, fmtTime } from '../api'
import { PageHeader, Pill, Section, Spinner } from '../components/ui'
import { useCase } from '../context'
import { useApi } from '../hooks'

function CopyBtn({ text }: { text: string }) {
  const [ok, setOk] = useState(false)
  return <button className="btn px-2 py-1 text-[11px]" onClick={() => { navigator.clipboard.writeText(text); setOk(true); setTimeout(() => setOk(false), 1500) }}>{ok ? <Check size={12} /> : <Copy size={12} />}{ok ? 'Copied' : 'Copy'}</button>
}

export default function Complaint() {
  const { caseId, tick, toast } = useCase()
  const { data, reload, setData } = useApi<any>(`/api/cases/${caseId}/complaint`, [tick])
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [ack, setAck] = useState('')
  const [stage, setStage] = useState<'ready' | 'review' | 'submit'>('ready')
  useEffect(() => { if (data) setEdits(data.fields?.user_edits ?? {}) }, [data])
  if (!data) return <Spinner label="Preparing complaint" />
  const f = data.fields.fields
  const val = (k: string) => edits[k] ?? f[k] ?? ''
  const submitted = data.state === 'submitted'
  const save = async () => { const r = await api.put(`/api/cases/${caseId}/complaint`, { edits }); setData(r); toast('Edits saved'); setStage('submit') }

  const steps = [{ k: 'ready', t: 'Complaint ready' }, { k: 'review', t: 'Review / edit' }, { k: 'submit', t: 'Submit (by you)' }] as const
  return (
    <div className="max-w-5xl">
      <PageHeader step="Report" title="Complaint & reporting"
        question="Everything the NCRP portal and platforms ask for, prepared from the evidence. You review it, and you submit it yourself."
        right={<div className="flex gap-2">
          <button className="btn" onClick={async () => { await api.get(`/api/cases/${caseId}/complaint?regenerate=true`); reload(); toast('Regenerated from latest evidence') }} disabled={submitted}><RefreshCw size={14} /> Regenerate</button>
          <a className="btn" onClick={guardDemo} href={`/api/cases/${caseId}/complaint.docx`}><Download size={14} /> DOCX</a></div>} />

      <div className="mb-5 flex items-center gap-2">
        {steps.map((s, i) => {
          const done = submitted || steps.findIndex((x) => x.k === stage) > i
          const active = !submitted && stage === s.k
          return (
            <div key={s.k} className="flex flex-1 items-center gap-2">
              <button onClick={() => !submitted && setStage(s.k)} className={`flex flex-1 items-center gap-2 rounded-lg border px-4 py-3 text-left ${active ? 'border-ink bg-raised' : 'border-line'}`}>
                <span className={`flex h-6 w-6 items-center justify-center rounded-full text-[12px] font-bold ${done ? 'bg-ink text-bg' : 'border border-line-strong text-ink-3'}`}>{done ? <Check size={13} /> : i + 1}</span>
                <span className="font-semibold">{s.t}</span>
              </button>
              {i < 2 && <span className="text-ink-3">→</span>}
            </div>
          )
        })}
      </div>

      {submitted && (
        <div className="card mb-5 border-good p-4 text-[13px]">✓ You marked this complaint as submitted on {fmtTime(data.submitted_at)}. Acknowledgement number: <b className="mono">{data.ack_number}</b>. It is logged in the timeline and the custody log.</div>
      )}

      <Section title="Reporting route" className="mb-5">
        <div className="flex flex-wrap items-start gap-6 text-[13px]">
          <div className="flex-1"><div className="label">National Cyber Crime Reporting Portal</div><div className="mt-1 text-ink-2">{data.fields.route.description}</div></div>
          <div className="flex items-center gap-2"><Phone size={16} /> <span className="text-2xl font-semibold">{data.fields.route.helpline}</span><span className="text-[11px] text-ink-3">cyber crime<br />helpline</span></div>
        </div>
        <ul className="mt-3 list-disc space-y-0.5 pl-5 text-[12px] text-ink-3">{data.fields.notes.map((n: string) => <li key={n}>{n}</li>)}</ul>
      </Section>

      {(stage === 'ready' || stage === 'review') && !submitted && (
        <Section title={stage === 'ready' ? 'Prepared complaint' : 'Review and edit'} subtitle="Fields follow the portal's sections. Copy each block into the matching portal field."
          right={stage === 'ready' ? <button className="btn btn-primary" onClick={() => setStage('review')}>Review & edit →</button> : <button className="btn btn-primary" onClick={save}>Save & continue →</button>}>
          <div className="space-y-4">
            {[['incident_category', 'Incident category'], ['incident_datetime', 'Approximate date & time'], ['platforms', 'Platform(s) / where it occurred']].map(([k, l]) => (
              <Field key={k} label={l} text={val(k)} editing={stage === 'review'} onChange={(v) => setEdits({ ...edits, [k]: v })} />
            ))}
            <Field label={`Incident description (portal field · ${val('incident_description_short').length}/1500 chars)`} text={val('incident_description_short')} editing={stage === 'review'} rows={7} onChange={(v) => setEdits({ ...edits, incident_description_short: v })} />
            <Field label="Detailed statement (attach as document or paste where allowed)" text={val('incident_description_long')} editing={stage === 'review'} rows={14} mono onChange={(v) => setEdits({ ...edits, incident_description_long: v })} />
            <div>
              <div className="mb-1.5 flex items-center justify-between"><span className="label">Accounts / URLs observed (listed as observed — not as authors)</span><CopyBtn text={f.suspect_identifiers.map((s: any) => `${s.type}: ${s.value} (${s.platform})`).join('\n')} /></div>
              <div className="space-y-1">{f.suspect_identifiers.map((s: any, i: number) => (
                <div key={i} className="flex gap-3 rounded border border-line px-3 py-1.5 text-[12.5px]"><span className="w-56 shrink-0 text-ink-3">{s.type}</span><span className="text-ink-3">{s.platform}</span><span className="mono break-all">{s.value}</span></div>
              ))}</div>
            </div>
            <div>
              <div className="label mb-1.5">Evidence to attach</div>
              <div className="space-y-1">{f.evidence_list.map((e: any) => (
                <div key={e.sha256} className="flex items-center gap-3 rounded border border-line px-3 py-1.5 text-[12px]">
                  <FileText size={13} className="text-ink-3" /><span className="w-56 truncate">{e.file}</span><span className="text-ink-3">{e.kind}</span>
                  <span className="mono truncate text-[10.5px] text-ink-3">{e.sha256}</span><span className="ml-auto shrink-0 text-ink-3">{fmtBytes(e.size)}</span>
                  {e.ncrp_uploadable ? <Pill tone="good">portal-ready</Pill> : <Pill>package only</Pill>}
                </div>
              ))}</div>
            </div>
          </div>
        </Section>
      )}

      {stage === 'submit' && !submitted && (
        <Section title="Submit — you do this yourself" subtitle="This system never submits complaints, contacts the police or publishes anything for you.">
          <ol className="list-decimal space-y-2 pl-5 text-[13px] text-ink-2">
            <li>Open the portal and choose: <b className="text-ink">{data.fields.route.key === 'women_child' ? 'Report Women/Child Related Crime' : 'Report Other Cyber Crime'}</b>.</li>
            <li>Copy each prepared block into the matching field (go back to Review to copy).</li>
            <li>Attach the "portal-ready" files. Keep the full evidence package for the police.</li>
            <li>After submitting, note the acknowledgement number and record it below.</li>
          </ol>
          <a className="btn btn-primary mt-4" href={data.fields.route.portal} target="_blank" rel="noreferrer"><ExternalLink size={14} /> Open cybercrime.gov.in</a>
          <div className="mt-5 flex items-end gap-2">
            <div className="flex-1"><div className="label mb-1.5">Acknowledgement number from the portal</div><input className="input mono" value={ack} onChange={(e) => setAck(e.target.value)} placeholder="e.g. 31234567890123" /></div>
            <button className="btn" disabled={!ack.trim()} onClick={async () => { await api.post(`/api/cases/${caseId}/complaint/submitted`, { ack_number: ack.trim(), route: 'NCRP' }); reload(); toast('Recorded as submitted by you') }}>I submitted it — record</button>
          </div>
        </Section>
      )}

      <Section className="mt-5" title="Platform reports" subtitle="Report each live post to the platform as well. Ready-to-paste text for each one:">
        <div className="space-y-3">{data.fields.platform_reports.map((p: any) => (
          <div key={p.platform} className="rounded-lg border border-line p-3">
            <div className="flex items-center justify-between"><b>{p.platform}</b><div className="flex gap-2"><CopyBtn text={p.text} />{p.help && <a className="btn px-2 py-1 text-[11px]" href={p.help} target="_blank" rel="noreferrer"><ExternalLink size={12} /> Help centre</a>}</div></div>
            <div className="mt-1 text-[12px] text-ink-3">{p.how} {p.grievance}</div>
            <p className="mt-2 rounded bg-bg/60 p-2 text-[12.5px] text-ink-2">{p.text}</p>
          </div>
        ))}</div>
        <div className="mt-4 grid gap-2 md:grid-cols-2">{data.fields.other_routes.map((r: any) => (
          <a key={r.name} href={r.url} target="_blank" rel="noreferrer" className="rounded-lg border border-line p-3 text-[12.5px] hover:border-line-strong"><b>{r.name}</b> <span className="text-ink-3">— when {r.when}. {r.note}</span></a>
        ))}</div>
      </Section>

      <Section className="mt-5" title="Possibly relevant provisions" subtitle={data.fields.legal_disclaimer}>
        <div className="space-y-1.5">{data.fields.possibly_relevant_provisions.map((p: any) => (
          <div key={p.id} className="flex gap-3 rounded border border-line px-3 py-2 text-[12.5px]">
            <span className="w-64 shrink-0 font-medium">{p.law} — {p.section}</span>
            <span className="text-ink-2">{p.title}{p.relevance && <span className="block text-ink-3">{p.relevance}</span>}</span>
            {!p.last_verified && <span className="ml-auto shrink-0"><Pill tone="warn">confirm</Pill></span>}
          </div>
        ))}</div>
        <div className="mt-3 flex flex-wrap gap-3 text-[12px] text-ink-3">{data.fields.helplines.map((h: any) => <span key={h.name}>{h.name}: <b className="text-ink-2">{h.contact}</b></span>)}</div>
      </Section>
    </div>
  )
}

function Field({ label, text, editing, rows = 1, mono = false, onChange }: { label: string; text: string; editing: boolean; rows?: number; mono?: boolean; onChange: (v: string) => void }) {
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between"><span className="label">{label}</span><CopyBtn text={text} /></div>
      {editing
        ? (rows > 1 ? <textarea className={`input ${mono ? 'mono text-[11.5px]' : ''}`} rows={rows} value={text} onChange={(e) => onChange(e.target.value)} /> : <input className="input" value={text} onChange={(e) => onChange(e.target.value)} />)
        : <div className={`whitespace-pre-wrap rounded-lg border border-line bg-bg/50 px-3 py-2 text-[13px] text-ink-2 ${mono ? 'mono max-h-72 overflow-y-auto text-[11.5px]' : ''}`}>{text}</div>}
    </div>
  )
}
