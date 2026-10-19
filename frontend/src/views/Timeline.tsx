/* eslint-disable @typescript-eslint/no-explicit-any */
import { AlertTriangle } from 'lucide-react'
import { useMemo, useState } from 'react'
import { fmtTime, type Status } from '../api'
import { PageHeader, Section, Spinner, STATUS, StatusBadge } from '../components/ui'
import { useCase } from '../context'
import { useApi } from '../hooks'

interface Ev { at: string | null; end: string | null; title: string; lane: string; status: Status; time_source: string; precision: string; detail: string; kind: string }
const LANE_LABEL: Record<string, string> = {
  provenance: 'Creation & provenance', publication: 'Publication & spread', archive: 'Third-party archives',
  investigation: 'Investigation', reporting: 'Reporting', monitoring: 'Monitoring',
}

const parse = (s: string) => new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : s.replace(' ', 'T') + 'Z').getTime()

export default function Timeline() {
  const { caseId, tick } = useCase()
  const { data } = useApi<{ events: Ev[]; lanes: string[]; conflicts: any[] }>(`/api/cases/${caseId}/timeline`, [tick])
  const [hover, setHover] = useState<{ e: Ev; x: number; y: number } | null>(null)

  const g = useMemo(() => {
    if (!data) return null
    const dated = data.events.filter((e) => e.at)
    const ts = dated.map((e) => parse(e.at!))
    const t0 = Math.min(...ts), t1 = Math.max(...ts)
    const span0 = (t1 - t0) || 3600e3
    const padLeft = span0 * 0.16, padRight = span0 * 0.04 // room on the left for the "unknown creation" band
    const W = 1100, padL = 190, padR = 30, laneH = 62, top = 16
    const lanes = data.lanes.filter((l) => data.events.some((e) => e.lane === l))
    const x = (t: number) => padL + ((t - (t0 - padLeft)) / (t1 + padRight - (t0 - padLeft))) * (W - padL - padR)
    const ticks: number[] = []
    const span = t1 - t0
    const step = span > 60 * 86400e3 ? 7 * 86400e3 : span > 4 * 86400e3 ? 86400e3 : 6 * 3600e3
    for (let t = Math.ceil((t0 - padLeft) / step) * step; t <= t1 + padRight; t += step) ticks.push(t)
    // stagger events that collide within a lane
    const placed: Record<string, { x: number; row: number }[]> = {}
    const pos = data.events.map((e) => {
      if (!e.at) return null
      const px = x(parse(e.at))
      const arr = (placed[e.lane] ??= [])
      let row = 0
      while (arr.some((p) => p.row === row && Math.abs(p.x - px) < 16)) row++
      arr.push({ x: px, row })
      return { x: px, y: top + lanes.indexOf(e.lane) * laneH + laneH / 2 + (row % 3) * 13 - 10 }
    })
    return { W, H: top + lanes.length * laneH + 30, lanes, x, ticks, pos, padL, laneH, top, t0, step }
  }, [data])

  return (
    <div>
      <PageHeader step="Investigate" title="Incident reconstruction"
        question="What happened, in what order — and how well is each moment established? Ranges are shown where precision is low; unknowns are drawn, not hidden." />
      {!data || !g ? <Spinner /> : (
        <>
          {data.conflicts.length > 0 && (
            <div className="mb-4 space-y-2">{data.conflicts.map((c, i) => (
              <div key={i} className="flex gap-2 rounded-lg border border-warn/60 bg-warn/5 p-3 text-[13px]"><AlertTriangle size={15} className="text-warn" /><div><b>{c.title}.</b> <span className="text-ink-2">{c.detail}</span></div></div>
            ))}</div>
          )}
          <Section title="Swimlanes" subtitle="Marker style encodes status: filled = Verified, double ring = Corroborated, dashed = Inferred, dotted diamond = Lead, hatched band = Unknown.">
            <div className="relative overflow-x-auto">
              <svg viewBox={`0 0 ${g.W} ${g.H}`} className="w-full min-w-[900px]">
                <defs>
                  <pattern id="hatch" patternUnits="userSpaceOnUse" width="7" height="7" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="7" stroke="var(--color-st-unknown)" strokeWidth="2" opacity="0.55" /></pattern>
                </defs>
                {g.lanes.map((ln, i) => (
                  <g key={ln}>
                    <rect x={0} y={g.top + i * g.laneH} width={g.W} height={g.laneH} fill={i % 2 ? 'transparent' : 'color-mix(in srgb, var(--color-ink) 4%, transparent)'} />
                    <text x={10} y={g.top + i * g.laneH + g.laneH / 2 + 4} fontSize={12} fontWeight={600} fill="var(--color-ink-2)">{LANE_LABEL[ln] ?? ln}</text>
                  </g>
                ))}
                {g.ticks.map((t) => (
                  <g key={t}>
                    <line x1={g.x(t)} x2={g.x(t)} y1={g.top} y2={g.H - 24} stroke="var(--color-line)" />
                    <text x={g.x(t)} y={g.H - 8} fontSize={10} textAnchor="middle" fill="var(--color-ink-3)">
                      {new Date(t).toLocaleString('en-IN', { day: '2-digit', month: 'short', ...(g.step < 86400e3 ? { hour: '2-digit' } : {}), timeZone: 'Asia/Kolkata' })}
                    </text>
                  </g>
                ))}
                {data.events.map((e, i) => {
                  if (!e.at && e.end) {
                    const lane = g.lanes.indexOf(e.lane)
                    const xe = g.x(parse(e.end))
                    return (
                      <g key={i} onMouseEnter={() => setHover({ e, x: g.padL + 10, y: g.top + lane * g.laneH })} onMouseLeave={() => setHover(null)}>
                        <rect x={g.padL} y={g.top + lane * g.laneH + 8} width={Math.max(4, xe - g.padL)} height={g.laneH - 16} fill="url(#hatch)" stroke="var(--color-st-unknown)" strokeDasharray="2 4" rx={6} />
                        <text x={g.padL + 8} y={g.top + lane * g.laneH + g.laneH / 2 + 4} fontSize={11} fill="var(--color-ink)" fontWeight={600}>? {e.title} — unknown</text>
                      </g>
                    )
                  }
                  const p = g.pos[i]
                  if (!p) return null
                  const short = ['published', 'removed', 'report_submitted', 'earliest_discovered'].includes(e.kind) ? (e.title.split('— ').pop() ?? '').replace(/\[DEMO\]/g, '').trim() : ''
                  return (
                    <g key={i} className="cursor-pointer" onMouseEnter={() => setHover({ e, x: p.x, y: p.y })} onMouseLeave={() => setHover(null)}>
                      <Marker s={e.status} x={p.x} y={p.y} approx={e.precision !== 'exact'} />
                      {short && e.kind !== 'earliest_discovered' && <text x={p.x} y={p.y - 12} fontSize={9.5} textAnchor="middle" fill="var(--color-ink-2)">{short.length > 22 ? short.slice(0, 21) + '…' : short}</text>}
                    </g>
                  )
                })}
              </svg>
              {hover && (
                <div className="card pointer-events-none absolute z-10 w-80 p-3 text-[12px] shadow-xl" style={{ left: `min(${(hover.x / g.W) * 100}%, calc(100% - 330px))`, top: hover.y + 22 }}>
                  <div className="flex items-center gap-2"><StatusBadge status={hover.e.status} /><span className="font-semibold">{hover.e.title}</span></div>
                  <div className="mt-1 text-ink-2">{hover.e.at ? fmtTime(hover.e.at) : `before ${fmtTime(hover.e.end)}`}</div>
                  <div className="text-ink-3">Source: {hover.e.time_source} · precision: {hover.e.precision}</div>
                  {hover.e.detail && <div className="mt-1 text-ink-2">{hover.e.detail}</div>}
                </div>
              )}
            </div>
          </Section>
          <Section className="mt-5" title="Chronology (table view)" subtitle="The same events as text, with the source and precision of every time.">
            <table className="w-full text-[12.5px]">
              <thead><tr className="border-b border-line text-left text-ink-3"><th className="py-2 pr-3 font-medium">Time (IST)</th><th className="pr-3 font-medium">Event</th><th className="pr-3 font-medium">Status</th><th className="font-medium">Time source</th></tr></thead>
              <tbody>{data.events.map((e, i) => (
                <tr key={i} className="border-b border-line/60 align-top">
                  <td className="mono whitespace-nowrap py-2 pr-3 text-[11.5px] text-ink-2">{e.at ? fmtTime(e.at, false) : `before ${fmtTime(e.end, false)}`}</td>
                  <td className="pr-3">{e.title}{e.detail && <div className="text-[11.5px] text-ink-3">{e.detail}</div>}</td>
                  <td className="pr-3"><StatusBadge status={e.status} /></td>
                  <td className="text-ink-3">{e.time_source}{e.precision !== 'exact' && <div className="text-[11px]">{e.precision}</div>}</td>
                </tr>
              ))}</tbody>
            </table>
          </Section>
        </>
      )}
    </div>
  )
}

function Marker({ s, x, y, approx }: { s: Status; x: number; y: number; approx: boolean }) {
  const c = `var(--color-st-${s.toLowerCase()})`
  const ring = approx ? <circle cx={x} cy={y} r={11} fill="none" stroke={c} strokeOpacity={0.35} strokeWidth={6} /> : null
  switch (s) {
    case 'Verified': return <>{ring}<circle cx={x} cy={y} r={7} fill={c} stroke="var(--color-surface)" strokeWidth={2} /></>
    case 'Corroborated': return <>{ring}<circle cx={x} cy={y} r={8} fill="var(--color-surface)" stroke={c} strokeWidth={2} /><circle cx={x} cy={y} r={4} fill={c} /></>
    case 'Inferred': return <>{ring}<circle cx={x} cy={y} r={7} fill="var(--color-surface)" stroke={c} strokeWidth={2.2} strokeDasharray="3 2" /></>
    case 'Lead': return <>{ring}<rect x={x - 6} y={y - 6} width={12} height={12} transform={`rotate(45 ${x} ${y})`} fill="var(--color-surface)" stroke={c} strokeWidth={2} strokeDasharray="1.5 2" /></>
    default: return <circle cx={x} cy={y} r={7} fill="url(#hatch)" stroke={STATUS.Unknown.color} strokeWidth={1.5} />
  }
}
