/* eslint-disable @typescript-eslint/no-explicit-any */
import { AlarmClock, Check, ChevronDown, Copy, ExternalLink, FileText, Phone, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtTime } from '../api'
import { Empty, LoadError, PageHeader, Pill, Section, Spinner } from '../components/ui'
import { useCase } from '../context'
import { useApi } from '../hooks'
import { casePath } from '../lib/paths'

interface Rep {
  id: string; key: string; kind: string; title: string; target: string; status: string; reference: string; submitted_at?: string; due_at?: string
  clock_basis: string; notes: string; history: { at: string; status: string; note: string }[]; open: boolean; overdue: boolean; seconds_left: number | null; urls: string[]
}
interface Action {
  key: string; kind: string; title: string; why: string; steps: string[]; links: { label: string; url: string }[]; prepared_text: string | null
  urls: string[]; location_ids: string[]; clock: any; priority: number; target: string; report: Rep | null; done: boolean
}
interface Plan { actions: Action[]; reports: Rep[]; summary: { total: number; done: number; overdue: number; open: number; removed: number }; clock_sources: { as_of: string; note: string; sources: { name: string; url: string }[] }; disclaimer: string }

const STATUS: Record<string, { label: string; tone: 'neutral' | 'crit' | 'warn' | 'good' }> = {
  planned: { label: 'Not done yet', tone: 'neutral' }, submitted: { label: 'Submitted — waiting', tone: 'warn' }, acknowledged: { label: 'Acknowledged', tone: 'warn' },
  action_taken: { label: 'Action taken', tone: 'good' }, removed: { label: 'Removed ✓', tone: 'good' }, rejected: { label: 'Rejected', tone: 'crit' },
  escalated: { label: 'Escalated', tone: 'warn' }, closed: { label: 'Closed', tone: 'neutral' },
}
const KIND: Record<string, string> = { ncrp: 'Police complaint', platform: 'Platform report', stopncii: 'Block re-uploads', bank: 'Money', grievance_officer: 'Escalation', other: 'Other' }

const fmtLeft = (s: number) => {
  const a = Math.abs(s), d = Math.floor(a / 86400), h = Math.floor((a % 86400) / 3600), m = Math.floor((a % 3600) / 60)
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`
}
const toIso = (local: string) => (local ? new Date(local).toISOString() : undefined)
const nowLocal = () => { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16) }

function CopyBtn({ text }: { text: string }) {
  const [ok, setOk] = useState(false)
  return <button className="btn px-2 py-1 text-[11px]" onClick={() => { navigator.clipboard.writeText(text); setOk(true); setTimeout(() => setOk(false), 1500) }}>{ok ? <Check size={12} /> : <Copy size={12} />}{ok ? 'Copied' : 'Copy text'}</button>
}

function Due({ r }: { r: Rep }) {
  const [, force] = useState(0)
  useEffect(() => { const t = setInterval(() => force((x) => x + 1), 30000); return () => clearInterval(t) }, [])
  if (!r.due_at || !r.open) return null
  const left = Math.round((new Date(r.due_at).getTime() - Date.now()) / 1000)
  const over = left < 0 && ['submitted', 'acknowledged', 'action_taken'].includes(r.status)
  return (
    <span className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11.5px] font-semibold ${over ? 'border-crit bg-crit/10 text-crit' : 'border-line-strong text-ink-2'}`} title={r.clock_basis}>
      <AlarmClock size={12} /> {over ? `Overdue by ${fmtLeft(left)}` : `Due in ${fmtLeft(left)}`}
    </span>
  )
}

export default function Takedown() {
  const { caseId, tick, toast, refresh } = useCase()
  const { data, error, reload } = useApi<Plan>(`/api/cases/${caseId}/takedown`, [tick])
  const [open, setOpen] = useState<string | null>(null)
  if (error && !data) return <LoadError what="your action plan" error={error} onRetry={reload} />
  if (!data) return <Spinner label="Preparing your action plan" />
  const first = data.actions.find((a) => !a.done)?.key
  const cur = open ?? first ?? null
  const doReload = () => { reload(); refresh() }

  return (
    <div className="max-w-5xl">
      <PageHeader step="Take action" title="Get it removed and register your complaint"
        question="A step-by-step guide. You do each step yourself on the official site — we prepare the text, keep your reference numbers, and warn you when a deadline has passed."
        right={<Link className="btn" to={casePath(caseId, 'complaint')}><FileText size={14} /> Prepared complaint</Link>} />

      {data.summary.overdue > 0 && (
        <div className="mb-4 flex items-start gap-3 rounded-lg border border-crit bg-crit/10 p-4">
          <AlarmClock size={20} className="mt-0.5 shrink-0 text-crit" />
          <div><div className="font-semibold">{data.summary.overdue} report{data.summary.overdue > 1 ? 's' : ''} passed the deadline and may not be handled.</div>
            <div className="text-[13px] text-ink-2">Use the “write to the Grievance Officer” step below, then appeal to the Grievance Appellate Committee if needed.</div></div>
        </div>
      )}

      <div className="mb-4 grid gap-3 sm:grid-cols-4">
        {[['Steps done', `${data.summary.done} / ${data.summary.total}`], ['Waiting for a reply', data.summary.open], ['Overdue', data.summary.overdue], ['Confirmed removed', data.summary.removed]].map(([k, v]) => (
          <div key={k as string} className="card p-3"><div className="text-[11px] uppercase tracking-wider text-ink-3">{k}</div><div className={`mt-1 text-2xl font-semibold ${k === 'Overdue' && +v > 0 ? 'text-crit' : ''}`}>{v}</div></div>
        ))}
      </div>

      <div className="space-y-3">
        {data.actions.map((a, i) => (
          <ActionCard key={a.key} a={a} n={i + 1} caseId={caseId} open={cur === a.key} onToggle={() => setOpen(cur === a.key ? '' : a.key)} onChanged={doReload} toast={toast} />
        ))}
      </div>

      <Section className="mt-6" title="Your tracker" subtitle="Everything you reported, the reference numbers you were given, and what happened next.">
        {data.reports.length === 0 ? <Empty>Nothing recorded yet. After you complete a step above, record it there and it appears here.</Empty> : (
          <div className="overflow-x-auto">
            <table className="w-full text-[12.5px]">
              <thead><tr className="border-b border-line text-left text-ink-3"><th className="py-2 pr-3 font-medium">What</th><th className="pr-3 font-medium">Status</th><th className="pr-3 font-medium">Reference</th><th className="pr-3 font-medium">Submitted</th><th className="pr-3 font-medium">Deadline</th><th /></tr></thead>
              <tbody>
                {data.reports.map((r) => (
                  <tr key={r.id} className="border-b border-line/60 align-top">
                    <td className="py-2 pr-3"><div className="font-medium">{r.title}</div><div className="text-[11px] text-ink-3">{KIND[r.kind] ?? r.kind} · {r.target}</div></td>
                    <td className="pr-3"><Pill tone={STATUS[r.status]?.tone}>{STATUS[r.status]?.label ?? r.status}</Pill></td>
                    <td className="mono pr-3 text-[12px]">{r.reference || <span className="text-ink-3">—</span>}</td>
                    <td className="pr-3 text-ink-2">{fmtTime(r.submitted_at)}</td>
                    <td className="pr-3"><Due r={r} />{!r.open && <span className="text-ink-3">—</span>}</td>
                    <td className="text-right"><button className="btn px-2 py-1" title="Delete this record" onClick={async () => { if (confirm('Delete this tracker record? (Your timeline note stays.)')) { await api.delete(`/api/cases/${caseId}/reports/${r.id}`); doReload() } }}><Trash2 size={12} /></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <div className="mt-4 rounded-lg border border-line p-4 text-[12px] text-ink-3">
        <div className="mb-1 font-semibold text-ink-2">About the deadlines</div>
        Deadlines follow the IT Rules amendment of February 2026 (as of {data.clock_sources.as_of}). {data.clock_sources.note} {data.disclaimer}
        <div className="mt-1 flex flex-wrap gap-3">{data.clock_sources.sources.map((s) => <a key={s.url} className="underline" href={s.url} target="_blank" rel="noreferrer">{s.name}</a>)}</div>
        <div className="mt-2 flex items-center gap-2"><Phone size={13} /> Cyber crime helpline <b className="text-ink-2">1930</b> · Women helpline <b className="text-ink-2">181</b> · Childline <b className="text-ink-2">1098</b> · Emergency <b className="text-ink-2">112</b></div>
      </div>
    </div>
  )
}

function ActionCard({ a, n, caseId, open, onToggle, onChanged, toast }: { a: Action; n: number; caseId: string; open: boolean; onToggle: () => void; onChanged: () => void; toast: (m: string, t?: 'ok' | 'err') => void }) {
  const r = a.report
  const [ref, setRef] = useState(r?.reference ?? '')
  const [when, setWhen] = useState(nowLocal())
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  const save = async (status: string, extra: Record<string, any> = {}) => {
    setBusy(true)
    try {
      await api.post(`/api/cases/${caseId}/reports`, {
        key: a.key, kind: a.kind, title: a.title, target: a.target, location_ids: a.location_ids, status, reference: ref || undefined,
        submitted_at: status === 'submitted' ? toIso(when) : undefined, note: note || undefined, ...extra,
      })
      toast(status === 'submitted' ? 'Recorded — the deadline clock has started' : 'Updated')
      setNote('')
      onChanged()
    } catch (e) { toast((e as Error).message, 'err') } finally { setBusy(false) }
  }

  return (
    <div className={`card overflow-hidden ${a.done ? 'opacity-90' : ''} ${r?.overdue ? 'border-crit' : ''}`}>
      <button className="flex w-full items-center gap-3 p-4 text-left" onClick={onToggle}>
        <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[12px] font-bold ${a.done ? 'bg-good text-good-ink' : 'border border-line-strong text-ink-2'}`}>{a.done ? <Check size={14} /> : n}</span>
        <span className="min-w-0 flex-1"><span className="block font-semibold leading-snug">{a.title}</span><span className="text-[11.5px] text-ink-3">{KIND[a.kind] ?? a.kind}{a.target ? ` · ${a.target}` : ''}</span></span>
        {r && <><Pill tone={STATUS[r.status]?.tone}>{STATUS[r.status]?.label}</Pill><Due r={r} /></>}
        <ChevronDown size={16} className={`shrink-0 text-ink-3 transition ${open ? '' : '-rotate-90'}`} />
      </button>
      {open && (
        <div className="space-y-4 border-t border-line p-4 text-[13px]">
          <p className="text-ink-2">{a.why}</p>
          <ol className="list-decimal space-y-1.5 pl-5 text-ink-2">{a.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
          {a.links.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {a.links.map((l) => l.url === '@complaint'
                ? <Link key={l.label} className="btn" to={casePath(caseId, 'complaint')}><FileText size={13} /> {l.label}</Link>
                : <a key={l.label} className="btn btn-primary" href={l.url} target={l.url.startsWith('tel:') ? undefined : '_blank'} rel="noreferrer"><ExternalLink size={13} /> {l.label}</a>)}
            </div>
          )}
          {a.prepared_text && (
            <div><div className="mb-1 flex items-center justify-between"><span className="label">Text to paste into the report form</span><CopyBtn text={a.prepared_text} /></div>
              <p className="whitespace-pre-wrap rounded-lg border border-line bg-bg/50 p-3 text-[12.5px] text-ink-2">{a.prepared_text}</p></div>
          )}
          {a.urls.length > 0 && (
            <div><div className="label mb-1">Places this covers ({a.urls.length})</div>
              <ul className="space-y-0.5">{a.urls.map((u) => <li key={u} className="mono truncate text-[11.5px] text-ink-3">{u}</li>)}</ul></div>
          )}

          {!r || r.status === 'planned' ? (
            <div className="rounded-lg border border-line bg-bg/40 p-3">
              <div className="mb-2 font-semibold">Did you do this step?</div>
              <div className="grid gap-3 sm:grid-cols-[1fr_200px_auto] sm:items-end">
                <div><div className="label mb-1">Reference / ticket / acknowledgement number (if you got one)</div><input className="input mono" value={ref} onChange={(e) => setRef(e.target.value)} placeholder="e.g. 31234567890123" /></div>
                <div><div className="label mb-1">When</div><input type="datetime-local" className="input" value={when} onChange={(e) => setWhen(e.target.value)} /></div>
                <button className="btn btn-primary" disabled={busy} onClick={() => save('submitted')}>I did this{a.clock ? ' — start the clock' : ''}</button>
              </div>
              {a.clock && <p className="mt-2 text-[11.5px] text-ink-3"><AlarmClock size={11} className="mr-1 inline" />{a.clock.label} ({a.clock.hours ? `${a.clock.hours} hours` : `${a.clock.days} days`}). {a.clock.basis}</p>}
            </div>
          ) : (
            <div className="rounded-lg border border-line bg-bg/40 p-3">
              <div className="flex flex-wrap items-center gap-3">
                <span className="font-semibold">Submitted {fmtTime(r.submitted_at)}</span>{r.reference && <span className="mono rounded border border-line-strong px-2 py-0.5 text-[12px]">{r.reference}</span>}<Due r={r} />
              </div>
              <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_auto] sm:items-end">
                <div><div className="label mb-1">What happened next? (optional note)</div><input className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. “Instagram replied: under review”" /></div>
                <div className="flex flex-wrap gap-1.5">
                  {[['acknowledged', 'They replied'], ['action_taken', 'Action taken'], ['removed', 'It is removed ✓'], ['rejected', 'Rejected']].map(([s, l]) => (
                    <button key={s} className={`btn px-2.5 py-1 text-[12px] ${r.status === s ? 'border-ink' : ''}`} disabled={busy} onClick={() => save(s)}>{l}</button>
                  ))}
                </div>
              </div>
              {!r.reference && <div className="mt-3 flex gap-2"><input className="input mono" value={ref} onChange={(e) => setRef(e.target.value)} placeholder="Add the reference number you received" /><button className="btn" disabled={!ref || busy} onClick={() => save(r.status)}>Save</button></div>}
              {r.history.length > 0 && <ul className="mt-3 space-y-0.5 text-[11.5px] text-ink-3">{r.history.map((h, i) => <li key={i}>{fmtTime(h.at)} — {STATUS[h.status]?.label ?? h.status}{h.note ? `: ${h.note}` : ''}</li>)}</ul>}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
