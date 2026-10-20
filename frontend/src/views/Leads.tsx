/* eslint-disable @typescript-eslint/no-explicit-any */
import { Scale, ChevronDown, ExternalLink, Info } from 'lucide-react'
import { useState } from 'react'
import { fmtTime } from '../api'
import { Empty, PageHeader, Pill, Section, SensitiveImg, Spinner } from '../components/ui'
import { useCase } from '../context'
import { useApi } from '../hooks'

const CONF: Record<string, { tone: 'neutral' | 'crit' | 'warn' | 'good'; label: string }> = {
  high: { tone: 'good', label: 'High confidence' }, moderate: { tone: 'warn', label: 'Moderate confidence' }, low: { tone: 'neutral', label: 'Low confidence' },
}
const TYPE: Record<string, string> = {
  first_uploader: 'First known uploader', closest_to_source: 'Closest to source', altered_version: 'Altered version', sequence: 'Upload sequence',
  link: 'Possible link between accounts', generator: 'Generator', shared_artifact: 'Shared artefact',
}

export default function Leads() {
  const { caseId, tick, data: cd } = useCase()
  const { data, error } = useApi<any>(`/api/cases/${caseId}/lead-engine`, [tick])
  const [open, setOpen] = useState<string | null>(null)
  if (error && !data) return <div className="card max-w-2xl border-crit/60 p-5 text-sm">Could not build the leads: {error}</div>
  if (!data) return <Spinner label="Comparing posts, versions and accounts" />
  const first = data.leads[0]?.id
  const cur = open ?? first

  return (
    <div className="max-w-6xl">
      <PageHeader step="Investigate deeper" title="Connected accounts & leads"
        question="Who posted which version, when was each version first seen, what is directly observed and what is only a hypothesis — and what would confirm it?" />

      <div className="mb-5 flex gap-3 rounded-lg border border-warn/50 bg-warn/5 p-4 text-[13px] text-ink-2">
        <Scale size={18} className="mt-0.5 shrink-0 text-warn" />
        <div><b className="text-ink">Leads, not findings.</b> {data.principle}</div>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2 text-[13px]">
        <Pill>{data.summary.accounts} account{data.summary.accounts === 1 ? '' : 's'}</Pill><Pill>{data.summary.posts} post{data.summary.posts === 1 ? '' : 's'}</Pill>
        <Pill>{data.summary.versions} version{data.summary.versions === 1 ? '' : 's'}</Pill><Pill tone={data.summary.leads ? 'good' : 'neutral'}>{data.summary.leads} lead{data.summary.leads === 1 ? '' : 's'}</Pill>
        <span className="text-ink-3">{data.summary.time_quality}</span>
      </div>

      {data.summary.posts === 0 ? (
        <Empty>No captured posts yet. Open <b>Where it appeared</b> and add the links where the media was seen — each account that posted it becomes part of this analysis.</Empty>
      ) : (<>
        <Section title="Who posted what — and in which role" subtitle="Roles describe observed behaviour only. Nobody is named as the creator or the editor; those are not established by public copies.">
          <div className="grid gap-3 lg:grid-cols-2">
            {data.accounts.map((a: any) => (
              <div key={a.key} className="rounded-lg border border-line p-3">
                <div className="flex items-center justify-between"><div className="font-semibold">{a.label} <span className="text-[12px] font-normal text-ink-3">· {a.platform}{a.kind === 'site' ? ' (a website, not an account)' : ''}</span></div></div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {a.roles.map((r: any, i: number) => <span key={i} title={r.basis} className="inline-flex items-center gap-1"><Pill tone={CONF[r.confidence]?.tone}>{r.role}</Pill><span className="text-[10.5px] text-ink-3">{r.confidence}</span></span>)}
                  {a.roles.length === 0 && <span className="text-[12px] text-ink-3">No role could be determined.</span>}
                </div>
                <ul className="mt-2 space-y-1 text-[12px] text-ink-2">
                  {a.posts.map((p: any) => (
                    <li key={p.id} className="flex flex-wrap items-baseline gap-x-2"><span className="mono text-[11px] text-ink-3">{p.time ? fmtTime(p.time) : 'time unknown'}</span>
                      <span className={`text-[10.5px] ${p.time_basis === 'capture time only' ? 'text-warn' : 'text-ink-3'}`}>({p.time_basis})</span>
                      <span>{p.versions.join(', ')}</span><a className="text-ink-3 hover:text-ink" href={p.url} target="_blank" rel="noreferrer"><ExternalLink size={11} /></a></li>
                  ))}
                </ul>
                <div className="mt-2 text-[11px] text-ink-3">Not established: {a.not_established.join(' · ')}</div>
              </div>
            ))}
          </div>
          <details className="mt-3 text-[12px] text-ink-3"><summary className="cursor-pointer">What the roles mean</summary>
            <dl className="mt-2 grid gap-2 md:grid-cols-2">{data.roles_glossary.map((g: any) => <div key={g.role} className="rounded border border-line p-2"><dt className="font-semibold text-ink-2">{g.role}</dt><dd>{g.meaning}</dd><dd className="mt-0.5 text-ink-3">{g.status}</dd></div>)}</dl></details>
        </Section>

        <Section className="mt-4" title="Versions and their earliest appearance" subtitle="Each version is a distinct file. “Earliest” is the earliest this search observed — not proof of where it began.">
          <div className="space-y-3">
            {data.versions.map((v: any) => (
              <div key={v.id} className="grid gap-4 rounded-lg border border-line p-3 md:grid-cols-[90px_1fr]">
                <SensitiveImg caseId={caseId} sha={v.thumb} blur={cd.blur} className="h-20 w-20 rounded bg-black" />
                <div className="text-[12.5px]">
                  <div className="flex flex-wrap items-center gap-2"><b>{v.label}</b><span className="text-ink-3">{v.width ? `${v.width}×${v.height}` : ''} · {(v.mime || '').split('/')[1]?.toUpperCase()}</span>
                    {v.parent ? <Pill>{`differs from ${v.parent.label}`}</Pill> : <Pill tone="good">not derived from another version found</Pill>}</div>
                  {v.parent && <div className="mt-0.5 text-ink-2">{v.parent.differences.join(' · ') || 'same picture, different file'} <span className="text-ink-3">(direction inferred, not proven)</span></div>}
                  {v.earliest && <div className="mt-1 text-ink-2">Earliest seen: <b>{v.earliest.account}</b> at {fmtTime(v.earliest.time)} <span className={v.earliest.basis === 'capture time only' ? 'text-warn' : 'text-ink-3'}>({v.earliest.basis})</span></div>}
                  {v.appearances.length > 1 && <div className="mt-1 text-[11.5px] text-ink-3">Also posted by: {v.appearances.slice(1).map((a: any) => `${a.account}${a.time ? ` (${fmtTime(a.time, false)})` : ''}`).join(', ')}</div>}
                </div>
              </div>
            ))}
          </div>
        </Section>

        <Section className="mt-4" title="Relationship graph: observed vs hypothesis" subtitle="Solid lines were observed directly. Dashed amber lines are hypotheses and need authorised evidence before anyone relies on them."
          right={<GraphLegend />}>
          <LeadGraph g={data.graph} caseId={caseId} blur={cd.blur} versions={data.versions} onPick={(id: string) => setOpen(id)} />
        </Section>

        <div className="mt-5 flex items-end justify-between"><h2 className="text-lg font-semibold">Ranked leads</h2><span className="text-[12px] text-ink-3">Ranked by value to the investigation × how well supported</span></div>
        {data.leads.length === 0 && <div className="mt-2"><Empty>No leads yet — one post is not enough to compare. Add more places where the media appeared.</Empty></div>}
        <div className="mt-2 space-y-3" id="leads">
          {data.leads.map((l: any) => (
            <div key={l.id} className={`card overflow-hidden ${cur === l.id ? 'border-line-strong' : ''}`}>
              <button className="flex w-full items-start gap-3 p-4 text-left" onClick={() => setOpen(cur === l.id ? '' : l.id)}>
                <span className="mono mt-0.5 text-[11px] text-ink-3">#{l.priority}</span>
                <span className="min-w-0 flex-1"><span className="block font-semibold leading-snug">{l.headline}</span>
                  <span className="text-[11.5px] text-ink-3">{TYPE[l.type] ?? l.type} · {l.observed_count} observed · {l.hypothesis_count} hypothesis</span></span>
                <Pill tone={CONF[l.confidence]?.tone}>{CONF[l.confidence]?.label}</Pill>
                <ChevronDown size={16} className={`mt-0.5 shrink-0 text-ink-3 transition ${cur === l.id ? '' : '-rotate-90'}`} />
              </button>
              {cur === l.id && (
                <div className="space-y-4 border-t border-line p-4 text-[13px]">
                  <div className="flex items-start gap-2 rounded-lg bg-bg/50 p-3 text-ink-2"><Info size={14} className="mt-0.5 shrink-0" /><span><b>Why this confidence:</b> {l.confidence_reason}</span></div>
                  <div className="grid gap-4 md:grid-cols-2">
                    <div><div className="label mb-1.5">Evidence</div>
                      <ul className="space-y-1.5">{l.evidence.map((e: any, i: number) => (
                        <li key={i} className="flex gap-2"><span className={`mt-0.5 h-fit shrink-0 rounded border px-1.5 text-[10px] uppercase tracking-wide ${e.kind === 'observed' ? 'border-good/60 text-good' : 'border-warn/60 text-warn'}`}>{e.kind}</span><span className="text-ink-2">{e.text}</span></li>))}</ul></div>
                    <div><div className="label mb-1.5">Other explanations — and how to test them</div>
                      <ul className="space-y-2">{l.alternatives.map((a: any, i: number) => <li key={i} className="text-ink-2">{a.explanation}<span className="block text-[11.5px] text-ink-3">Test: {a.how_to_test}</span></li>)}</ul></div>
                  </div>
                  <div className="grid gap-4 md:grid-cols-2">
                    <div><div className="label mb-1.5">Authorised evidence that would CONFIRM it</div>
                      <ul className="list-disc space-y-1 pl-4 text-ink-2">{l.would_confirm.map((c: any) => <li key={c.key}>{c.text}</li>)}</ul></div>
                    <div><div className="label mb-1.5">Evidence that would DISPROVE it</div>
                      <ul className="list-disc space-y-1 pl-4 text-ink-2">{l.would_disprove.map((c: any) => <li key={c.key}>{c.text}</li>)}</ul></div>
                  </div>
                  <ul className="space-y-1 text-[12px] text-ink-3">{l.cautions.map((c: string) => <li key={c}>⚠ {c}</li>)}</ul>
                </div>
              )}
            </div>
          ))}
        </div>
        <div className="mt-4 rounded-lg border border-line p-4 text-[12px] text-ink-3">
          <div className="mb-1 font-semibold text-ink-2">Limits of this analysis</div>
          <ul className="list-disc space-y-0.5 pl-4">{data.limits.map((x: string) => <li key={x}>{x}</li>)}</ul>
        </div>
      </>)}
    </div>
  )
}

function GraphLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px] text-ink-3">
      <span className="flex items-center gap-1.5"><svg width="30" height="8"><line x1="0" y1="4" x2="30" y2="4" stroke="var(--color-ink)" strokeWidth="2.4" /></svg>observed</span>
      <span className="flex items-center gap-1.5"><svg width="30" height="8"><line x1="0" y1="4" x2="30" y2="4" stroke="var(--color-warn)" strokeWidth="2.4" strokeDasharray="6 5" /></svg>hypothesis</span>
    </div>
  )
}

function LeadGraph({ g, caseId, blur, versions, onPick }: { g: any; caseId: string; blur: boolean; versions: any[]; onPick: (id: string) => void }) {
  const accts = g.nodes.filter((n: any) => n.type === 'account')
  const vers = g.nodes.filter((n: any) => n.type === 'version')
  const AX = 150, VX = 620, AW = 210, VW = 220, RA = 86, RV = 96
  const ay = new Map<string, number>(accts.map((n: any, i: number) => [n.id, 24 + i * RA]))
  const vy = new Map<string, number>(vers.map((n: any, i: number) => [n.id, 24 + i * RV]))
  const H = Math.max(accts.length * RA, vers.length * RV) + 40
  const verInfo = new Map(versions.map((v) => [v.id, v]))
  const edges = g.edges.map((e: any, i: number) => {
    const sa = ay.has(e.source), ta = ay.has(e.target)
    let d = '', lx = 0, ly = 0
    if (sa && !ta) { const x1 = AX + AW, y1 = ay.get(e.source)! + 30, x2 = VX, y2 = (vy.get(e.target) ?? 0) + 34; d = `M${x1},${y1} C${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2} ${x2},${y2}`; lx = (x1 + x2) / 2; ly = (y1 + y2) / 2 }
    else if (sa && ta) { const x = AX, y1 = ay.get(e.source)! + 30, y2 = ay.get(e.target)! + 30, bulge = 70 + Math.abs(y2 - y1) * 0.15; d = `M${x},${y1} C${x - bulge},${y1} ${x - bulge},${y2} ${x},${y2}`; lx = x - bulge * 0.72; ly = (y1 + y2) / 2 }
    else { const x = VX + VW, off = (((e.key as number | undefined) ?? i) % 3) * 10 , y1 = (vy.get(e.source) ?? 0) + 34, y2 = (vy.get(e.target) ?? 0) + 34, bulge = 56 + off; d = `M${x},${y1} C${x + bulge},${y1} ${x + bulge},${y2} ${x},${y2}`; lx = x + bulge * 0.75 + 46; ly = (y1 + y2) / 2 }
    return { ...e, d, lx, ly, key: i }
  })
  return (
    <div className="overflow-x-auto">
      <div className="relative" style={{ width: VX + VW + 150, height: H, minWidth: VX + VW + 150 }}>
        <div className="absolute text-[10.5px] uppercase tracking-wider text-ink-3" style={{ left: AX, top: 2 }}>Accounts</div>
        <div className="absolute text-[10.5px] uppercase tracking-wider text-ink-3" style={{ left: VX, top: 2 }}>Versions of the media</div>
        <svg className="absolute inset-0" width="100%" height="100%">
          <defs><marker id="lg-arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="var(--color-ink)" /></marker></defs>
          {edges.map((e: any) => (
            <g key={e.key} className={e.lead ? 'cursor-pointer' : ''} onClick={() => e.lead && onPick(e.lead)}>
              <title>{e.evidence}</title>
              <path d={e.d} fill="none" stroke={e.kind === 'observed' ? 'var(--color-ink)' : 'var(--color-warn)'} strokeWidth={e.kind === 'observed' ? (e.label.startsWith('posted') ? 1.6 : 2.4) : 2.4}
                strokeDasharray={e.kind === 'hypothesis' ? '6 5' : undefined} opacity={e.label.startsWith('posted') ? 0.75 : 1} markerEnd={e.direction && e.direction === e.target ? 'url(#lg-arr)' : undefined} />
              {!e.label.startsWith('posted') && (
                <g transform={`translate(${e.lx},${e.ly})`}><rect x={-44} y={-9} width={88} height={17} rx={8} fill="var(--color-bg)" stroke={e.kind === 'observed' ? 'var(--color-line-strong)' : 'var(--color-warn)'} />
                  <text textAnchor="middle" y={3.5} fontSize={10} fill="var(--color-ink-2)">{e.label}</text></g>)}
            </g>
          ))}
        </svg>
        {accts.map((n: any) => (
          <div key={n.id} className="card absolute p-2.5" style={{ left: AX, top: ay.get(n.id), width: AW, height: 64 }}>
            <div className="truncate text-[13px] font-semibold">{n.label}</div><div className="truncate text-[11px] text-ink-3">{n.platform}</div>
            <div className="mt-0.5 truncate text-[10.5px] text-ink-2">{n.roles.length ? n.roles[0] : 'role not determined'}</div>
          </div>
        ))}
        {vers.map((n: any) => (
          <div key={n.id} className="card absolute flex gap-2 p-2" style={{ left: VX, top: vy.get(n.id), width: VW, height: 72 }}>
            <SensitiveImg caseId={caseId} sha={n.thumb} blur={blur} className="h-14 w-14 shrink-0 rounded bg-black" />
            <div className="min-w-0 text-[12px]"><div className="font-semibold">{n.label}</div><div className="truncate text-[10.5px] text-ink-3">{verInfo.get(n.id)?.width ? `${verInfo.get(n.id).width}×${verInfo.get(n.id).height}` : ''}</div>
              <div className="truncate text-[10.5px] text-ink-3">{verInfo.get(n.id)?.earliest ? `first seen ${fmtTime(verInfo.get(n.id).earliest.time, false)}` : ''}</div></div>
          </div>
        ))}
      </div>
    </div>
  )
}
