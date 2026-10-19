/* eslint-disable @typescript-eslint/no-explicit-any */
import { ExternalLink, EyeOff, Info, Plus, Radar, Search, Undo2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtTime } from '../api'
import { Empty, LoadError, Pill, SensitiveImg, Section, Spinner, PageHeader } from '../components/ui'
import { VerdictBadge } from '../components/VerdictCard'
import { useCase } from '../context'
import { useApi } from '../hooks'
import { casePath } from '../lib/paths'
import { StateTag } from './Propagation'

interface MNode { id: string; label: string; role: string; modality: string; thumb?: string; width?: number; height?: number; size: number; mime: string; time?: string; locations: string[]; verdict?: any; analysed: boolean }
interface LNode { id: string; url: string; platform: string; domain: string; handle: string; state: string; published_at?: string; first_captured_at?: string; published_source: string; discovered_via: string; depth: number; seed: boolean; dismissed: boolean; screenshot_sha: string; relation: string; relation_reason: string; media_ids: string[]; caption: string }
interface MEdge { id: string; a: string; b: string; tier: string; status: 'Verified' | 'Lead'; direction: 'a_to_b' | 'b_to_a' | null; from: string; to: string; differences: string[]; meaning: string; notes: string[]; scores: Record<string, number> }
interface SpreadData {
  media: MNode[]; locations: LNode[]; media_edges: MEdge[]; counts: Record<string, number>; primary_media: string | null
  search_links: { name: string; url: string; prefilled: boolean; best_for: string }[]; search_links_prefilled: boolean
  legend: Record<string, string>; coverage: string; clusters: { media_ids: string[]; earliest_found: string | null; largest_found: string; note: string }[]
}

const REL: Record<string, { label: string; tone: 'neutral' | 'crit' | 'warn' | 'good' }> = {
  seed: { label: 'Your link', tone: 'neutral' }, verified_copy: { label: 'Verified copy', tone: 'good' }, similar: { label: 'Looks similar — unverified', tone: 'warn' },
  no_match: { label: 'Different picture', tone: 'neutral' }, no_media: { label: 'No media readable', tone: 'neutral' }, pending: { label: 'Not captured yet', tone: 'neutral' },
  dismissed: { label: 'Not included', tone: 'neutral' },
}
const VIA: Record<string, string> = { manual: 'added by you', page_links: 'link on a captured page', duckduckgo: 'web search', serpapi_lens: 'image search', tineye: 'image search', serpapi_web: 'web search' }
const fmtSize = (n: number) => (n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`)
const short = (u: string, n = 52) => { try { const x = new URL(u); return (x.host + x.pathname).replace(/\/$/, '').slice(0, n) } catch { return u.slice(0, n) } }

const COL = { a: 0, b: 330, c: 690 }, W = { a: 250, b: 270, c: 290 }, ROW = 124, LROW = 96, TOP = 16

export default function Spread() {
  const { caseId, data: cd, tick, toast, refresh } = useCase()
  const { data, error, reload } = useApi<SpreadData>(`/api/cases/${caseId}/spread`, [tick])
  const [sel, setSel] = useState<{ t: 'm' | 'l'; id: string } | null>(null)
  const [form, setForm] = useState({ urls: '', find: true, depth: 2, open: false })
  const [busy, setBusy] = useState(false)
  const [showOut, setShowOut] = useState(false)
  const sensitive = cd.case.safety?.intimate || cd.case.safety?.minors_possible

  const L = useMemo(() => {
    if (!data) return null
    const mById = new Map(data.media.map((m) => [m.id, m]))
    const lById = new Map(data.locations.map((l) => [l.id, l]))
    const incl = data.locations.filter((l) => !l.dismissed && !(l.relation === 'pending' && l.discovered_via !== 'manual'))
    const inclMedia = new Set(incl.flatMap((l) => l.media_ids))
    const linked = new Set(data.media_edges.flatMap((e) => [e.a, e.b]))
    const prim = data.media.filter((m) => m.role === 'primary')
    const vers = data.media.filter((m) => m.role !== 'primary' && (inclMedia.has(m.id) || linked.has(m.id)) && (inclMedia.has(m.id) || [...data.media_edges].some((e) => (e.a === m.id || e.b === m.id) && e.status === 'Verified')))
      .sort((x, y) => (x.time ?? '9').localeCompare(y.time ?? '9'))
    const yB = new Map(vers.map((m, i) => [m.id, TOP + i * ROW]))
    const spanB = vers.length ? (vers.length - 1) * ROW : 0
    const yA = new Map(prim.map((m, i) => [m.id, TOP + spanB / 2 - ((prim.length - 1) * ROW) / 2 + i * ROW]))
    const yOf = (id: string) => yA.get(id) ?? yB.get(id)
    // places: ordered by the mean height of the media they show, then pushed apart
    const placed = incl.map((l) => {
      const ys = l.media_ids.map(yOf).filter((v): v is number => v != null)
      return { l, want: ys.length ? ys.reduce((s, v) => s + v, 0) / ys.length : 1e6 }
    }).sort((p, q) => p.want - q.want)
    const yC = new Map<string, number>()
    let cursor = TOP - LROW
    placed.forEach(({ l, want }) => {
      const y = Math.max(cursor + LROW, want === 1e6 ? cursor + LROW : want)
      yC.set(l.id, y)
      cursor = y
    })
    const height = Math.max(TOP * 2 + (vers.length ? (vers.length - 1) * ROW + 110 : 110), cursor + LROW + 20)
    return { mById, lById, incl, prim, vers, yA, yB, yC, height, yOf }
  }, [data])

  if (error && !data) return <LoadError what="the spread map" error={error} onRetry={reload} />
  if (!data || !L) return <Spinner label="Drawing the map" />
  const verifiedCopies = data.counts.verified_copy ?? 0
  const waiting = data.locations.filter((l) => !l.dismissed && l.relation === 'pending' && l.discovered_via !== 'manual').length
  const notIncluded = data.locations.filter((l) => l.dismissed)
  const xOf = (id: string) => (L.yA.has(id) ? COL.a : COL.b)
  const wOf = (id: string) => (L.yA.has(id) ? W.a : W.b)
  const mid = (y: number) => y + 40

  const edgePaths = [
    ...data.media_edges.filter((e) => L.yOf(e.a) != null && L.yOf(e.b) != null && L.yA.has(e.a) !== L.yA.has(e.b)).map((e) => {
      const [p, q] = L.yA.has(e.a) ? [e.a, e.b] : [e.b, e.a]
      const x1 = COL.a + W.a, y1 = mid(L.yA.get(p)!), x2 = COL.b, y2 = mid(L.yB.get(q)!)
      const dir = e.direction ? (e.from === p ? 'fwd' : 'back') : null
      return { key: e.id, x1, y1, x2, y2, kind: e.status === 'Verified' ? 'verified' : 'lead', dir, label: e.status === 'Verified' ? 'same content' : 'unverified', edge: e }
    }),
    ...L.incl.flatMap((l) => l.media_ids.filter((id) => L.yOf(id) != null).map((id) => ({
      key: `${l.id}-${id}`, x1: xOf(id) + wOf(id), y1: mid(L.yOf(id)!), x2: COL.c, y2: mid(L.yC.get(l.id)!) - 8, kind: 'captured', dir: null, label: '', edge: null as MEdge | null,
    }))),
  ]
  const curve = (x1: number, y1: number, x2: number, y2: number) => { const m = (x1 + x2) / 2; return `M${x1},${y1} C${m},${y1} ${m},${y2} ${x2},${y2}` }

  const run = async () => {
    const urls = form.urls.split(/\s+/).map((u) => u.trim()).filter(Boolean)
    setBusy(true)
    try {
      await api.post(`/api/cases/${caseId}/investigate`, { urls, find_copies: form.find && !sensitive, depth: urls.length && !form.open ? 0 : form.depth })
      toast(urls.length ? 'Capturing the links you added…' : 'Searching for more copies…')
      setForm({ ...form, urls: '' })
      refresh()
    } catch (e) { toast((e as Error).message, 'err') } finally { setBusy(false) }
  }
  const toggle = async (l: LNode, dismissed: boolean) => {
    await api.post(`/api/cases/${caseId}/locations/${l.id}/dismiss`, { dismissed })
    toast(dismissed ? 'Removed from the case record' : 'Added to the case record')
    reload(); refresh()
  }

  const selM = sel?.t === 'm' ? L.mById.get(sel.id) : null
  const selL = sel?.t === 'l' ? L.lById.get(sel.id) : null

  return (
    <div>
      <PageHeader step="Find where it spread" title="Where it appeared"
        question="Which places show this same picture or video, how does each version differ from the original you found, and how sure are we about each link?"
        right={<button className="btn btn-primary" onClick={() => setForm({ ...form, open: !form.open })}><Radar size={14} /> Find more copies</button>} />

      <div className="mb-4 flex flex-wrap items-center gap-2 text-[13px]">
        <Pill tone={verifiedCopies ? 'good' : 'neutral'}>{verifiedCopies} verified cop{verifiedCopies === 1 ? 'y' : 'ies'}</Pill>
        <Pill>{L.incl.length} place{L.incl.length === 1 ? '' : 's'} in the case record</Pill>
        {waiting > 0 && <Pill tone="warn">{waiting} candidate{waiting === 1 ? '' : 's'} waiting to be checked</Pill>}
        {!!data.counts.similar && <Pill tone="warn">{data.counts.similar} look similar (unverified)</Pill>}
        {notIncluded.length > 0 && <button className="text-ink-3 underline" onClick={() => setShowOut(!showOut)}>{notIncluded.length} checked, not included</button>}
        <span className="text-ink-3">· {data.coverage.split('.')[0]}.</span>
      </div>

      {form.open && (
        <Section className="mb-4" title="Find more copies" subtitle="We follow links on the pages we captured, and (if you allow it) search the web by the caption. A page only counts as a copy once its picture is verified to match.">
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <div className="label mb-1.5">Links you found yourself (optional, one per line)</div>
              <textarea className="input mono min-h-24 text-[12px]" placeholder={'https://…'} value={form.urls} onChange={(e) => setForm({ ...form, urls: e.target.value })} />
            </div>
            <div className="space-y-3 text-[13px]">
              <label className={`flex items-start gap-2 ${sensitive ? 'opacity-60' : ''}`}><input type="checkbox" className="mt-1" disabled={sensitive} checked={form.find && !sensitive} onChange={(e) => setForm({ ...form, find: e.target.checked })} />
                <span>Search the web with the caption text{!sensitive && <span className="text-ink-3"> (sent to DuckDuckGo; the image goes only to image-search services you configured)</span>}{sensitive && <span className="text-ink-3"> — off for sensitive cases</span>}</span></label>
              <div><div className="label mb-1">How deep</div>
                <select className="input" value={form.depth} onChange={(e) => setForm({ ...form, depth: +e.target.value })}>
                  <option value={1}>Level 1 — pages linked from your link</option><option value={2}>Level 2 — also pages linked from the copies found</option><option value={3}>Level 3 — one step further</option>
                </select></div>
              <button className="btn btn-primary w-full justify-center" disabled={busy} onClick={run}><Search size={14} /> {busy ? 'Starting…' : form.urls.trim() ? 'Capture links & search' : 'Search for copies'}</button>
            </div>
          </div>
        </Section>
      )}

      <Section title="Spread map" subtitle="Left to right: the media you started with → each version found → the places it was seen. Click any box for details."
        right={<MapLegend />}>
        {!L.prim.length && !L.vers.length ? <Empty>No media yet. Paste a link or add a file to start.</Empty> : (
          <div className="overflow-x-auto">
            <div className="relative" style={{ width: COL.c + W.c + 8, height: L.height, minWidth: COL.c + W.c + 8 }}>
              <div className="absolute inset-x-0 top-[-2px] flex text-[10.5px] uppercase tracking-wider text-ink-3">
                <span style={{ position: 'absolute', left: COL.a }}>The media you started with</span>
                <span style={{ position: 'absolute', left: COL.b }}>Versions found</span>
                <span style={{ position: 'absolute', left: COL.c }}>Where it appeared</span>
              </div>
              <svg className="absolute inset-0" width="100%" height="100%">
                <defs>
                  <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--color-ink)" /></marker>
                </defs>
                {edgePaths.map((p) => (
                  <g key={p.key} className={p.edge ? 'cursor-pointer' : ''} onClick={() => p.edge && setSel({ t: 'm', id: L.yA.has(p.edge.a) ? p.edge.b : p.edge.a })}>
                    <path d={curve(p.x1, p.y1 + 14, p.x2, p.y2 + 14)} fill="none"
                      stroke={p.kind === 'verified' ? 'var(--color-ink)' : p.kind === 'lead' ? 'var(--color-warn)' : 'var(--color-line-strong)'}
                      strokeWidth={p.kind === 'captured' ? 1.5 : 2.4} strokeDasharray={p.kind === 'lead' ? '6 5' : p.kind === 'captured' ? '2 4' : undefined}
                      markerEnd={p.dir === 'fwd' ? 'url(#arr)' : undefined} markerStart={p.dir === 'back' ? 'url(#arr)' : undefined} opacity={p.kind === 'captured' ? 0.8 : 1} />
                    {p.label && (
                      <g transform={`translate(${(p.x1 + p.x2) / 2},${(p.y1 + p.y2) / 2 + 14})`}>
                        <rect x={-38} y={-9} width={76} height={17} rx={8} fill="var(--color-bg)" stroke={p.kind === 'verified' ? 'var(--color-line-strong)' : 'var(--color-warn)'} />
                        <text textAnchor="middle" y={3.5} fontSize={10} fill="var(--color-ink-2)">{p.label}</text>
                      </g>
                    )}
                  </g>
                ))}
              </svg>

              {[...L.prim, ...L.vers].map((m) => {
                const top = L.yA.get(m.id) ?? L.yB.get(m.id)!
                const isP = L.yA.has(m.id)
                const p0 = L.prim[0]?.id
                const e = isP ? undefined : data.media_edges.find((x) => (x.a === m.id && x.b === p0) || (x.b === m.id && x.a === p0))
                return (
                  <button key={m.id} onClick={() => setSel({ t: 'm', id: m.id })} style={{ left: isP ? COL.a : COL.b, top, width: isP ? W.a : W.b }}
                    className={`card absolute h-[104px] overflow-hidden p-2.5 text-left transition hover:border-line-strong ${sel?.id === m.id ? 'border-ink' : ''}`}>
                    <div className="flex gap-2.5">
                      <SensitiveImg caseId={caseId} sha={m.thumb} blur={cd.blur} className="h-16 w-16 shrink-0 rounded bg-black" />
                      <div className="min-w-0 flex-1">
                        <div className="text-[11px] font-semibold uppercase tracking-wide text-ink-3">{isP ? 'Suspect media' : 'Version'}</div>
                        <div className="text-[12px] text-ink-2">{m.width ? `${m.width}×${m.height}` : m.modality} · {(m.mime || '').split('/')[1]?.toUpperCase()} · {fmtSize(m.size)}</div>
                        {m.verdict ? <div className="mt-1"><VerdictBadge v={m.verdict} /></div> : <div className="mt-1 text-[11px] text-ink-3">{isP ? 'analysing…' : 'not analysed'}</div>}
                      </div>
                    </div>
                    {e && e.differences[0] && <div className="mt-1.5 truncate text-[11.5px] text-ink-2" title={e.differences.join(' · ')}>{e.differences.slice(0, 2).join(' · ')}</div>}
                  </button>
                )
              })}

              {L.incl.map((l) => {
                const r = REL[l.relation] ?? REL.pending
                return (
                  <button key={l.id} onClick={() => setSel({ t: 'l', id: l.id })} style={{ left: COL.c, top: L.yC.get(l.id), width: W.c }}
                    className={`card absolute h-[80px] overflow-hidden p-2.5 text-left transition hover:border-line-strong ${sel?.id === l.id ? 'border-ink' : ''} ${l.media_ids.length ? '' : 'border-dashed'}`}>
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-[13px] font-semibold">{l.platform || l.domain}{l.handle ? ` · @${l.handle}` : ''}</span>
                      <StateTag s={l.state} />
                    </div>
                    <div className="mono truncate text-[10.5px] text-ink-3">{short(l.url)}</div>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-ink-3">
                      <Pill tone={r.tone}>{r.label}</Pill>
                      <span>{fmtTime(l.published_at ?? l.first_captured_at, false)}{!l.published_at ? ' (captured)' : ''}</span>
                    </div>
                  </button>
                )
              })}
            </div>
          </div>
        )}
        <p className="mt-3 text-[12px] text-ink-3"><Info size={12} className="mr-1 inline" />“Earliest copy found” means earliest <i>this search</i> found — not proof of who made the original or where it was first posted. Arrows show a suggested direction only when one version is clearly a cropped or smaller form of the other.</p>
      </Section>

      {(selM || selL) && (
        <Section className="mt-4" title={selM ? (selM.role === 'primary' ? 'Suspect media' : 'Version') : (selL!.platform || selL!.domain)}
          right={<button className="btn px-2 py-1 text-[12px]" onClick={() => setSel(null)}>Close</button>}>
          {selM && <MediaDetail m={selM} edges={data.media_edges} mById={L.mById} caseId={caseId} blur={cd.blur} places={data.locations.filter((l) => l.media_ids.includes(selM.id))} />}
          {selL && <PlaceDetail l={selL} caseId={caseId} blur={cd.blur} onToggle={toggle} />}
        </Section>
      )}

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Section title="Search by image yourself" subtitle="These sites often find copies that automatic search cannot. We cannot read their results — open one, then paste any matching pages above.">
          <div className="grid gap-2 sm:grid-cols-2">
            {data.search_links.map((s) => (
              <a key={s.name} href={s.url} target="_blank" rel="noreferrer" className="rounded-lg border border-line p-3 text-[13px] transition hover:border-line-strong">
                <div className="flex items-center justify-between font-semibold">{s.name} <ExternalLink size={13} className="text-ink-3" /></div>
                <div className="mt-0.5 text-[11.5px] text-ink-3">{s.best_for}</div>
                <div className="mt-1 text-[11px] text-ink-3">{s.prefilled ? 'Opens with your picture’s web address filled in.' : 'Open it, then upload the picture yourself.'}</div>
              </a>
            ))}
          </div>
          {sensitive && <p className="mt-2 text-[12px] text-warn">This case is sensitive: only use these if you are comfortable with the picture being uploaded to those sites.</p>}
        </Section>
        <Section title="How sure are we?" subtitle="Every link on the map carries its own confidence.">
          <ul className="space-y-2 text-[12.5px]">
            {Object.entries(data.legend).map(([k, v]) => <li key={k} className="flex gap-2"><Pill tone={k === 'Verified' ? 'good' : k === 'Lead' ? 'warn' : 'neutral'}>{k}</Pill><span className="text-ink-2">{v}</span></li>)}
          </ul>
          <p className="mt-3 text-[12px] text-ink-3">{data.coverage}</p>
        </Section>
      </div>

      <Section className="mt-4" title="Every place checked" subtitle="You decide what belongs in the case record (and therefore in the complaint and timeline).">
        <div className="overflow-x-auto">
          <table className="w-full text-[12.5px]">
            <thead><tr className="border-b border-line text-left text-ink-3"><th className="py-2 pr-3 font-medium">Place</th><th className="pr-3 font-medium">Result</th><th className="pr-3 font-medium">Found by</th><th className="pr-3 font-medium">State</th><th className="font-medium" /></tr></thead>
            <tbody>
              {data.locations.filter((l) => showOut || !l.dismissed).map((l) => {
                const r = REL[l.relation] ?? REL.pending
                return (
                  <tr key={l.id} className="border-b border-line/60 align-top">
                    <td className="max-w-sm py-2 pr-3"><div className="font-medium">{l.platform || l.domain}</div><div className="mono truncate text-[11px] text-ink-3" title={l.url}>{short(l.url, 70)}</div></td>
                    <td className="pr-3"><Pill tone={r.tone}>{r.label}</Pill><div className="mt-0.5 max-w-xs text-[11px] text-ink-3">{l.relation_reason}</div></td>
                    <td className="pr-3 text-ink-2">{VIA[l.discovered_via] ?? l.discovered_via}{l.depth > 0 ? ` · round ${l.depth}` : ''}</td>
                    <td className="pr-3"><StateTag s={l.state} /></td>
                    <td className="whitespace-nowrap py-1 text-right">
                      <a className="btn px-2 py-1 text-[11px]" href={l.url} target="_blank" rel="noreferrer"><ExternalLink size={11} /> Open</a>{' '}
                      {l.dismissed ? <button className="btn px-2 py-1 text-[11px]" onClick={() => toggle(l, false)}><Undo2 size={11} /> Include</button>
                        : !l.seed && <button className="btn px-2 py-1 text-[11px]" onClick={() => toggle(l, true)}><EyeOff size={11} /> Not related</button>}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {data.locations.length === 0 && <Empty>No places yet.</Empty>}
        </div>
        <div className="mt-3 flex items-center justify-between text-[12px] text-ink-3">
          <span>{notIncluded.length ? `${notIncluded.length} page(s) were checked but kept out of the case record.` : ''}</span>
          <Link className="underline" to={casePath(caseId, 'takedown')}>Next: get these removed →</Link>
        </div>
      </Section>
    </div>
  )
}

function MapLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px] text-ink-3">
      <span className="flex items-center gap-1.5"><svg width="30" height="8"><line x1="0" y1="4" x2="30" y2="4" stroke="var(--color-ink)" strokeWidth="2.4" /></svg>verified same content</span>
      <span className="flex items-center gap-1.5"><svg width="30" height="8"><line x1="0" y1="4" x2="30" y2="4" stroke="var(--color-warn)" strokeWidth="2.4" strokeDasharray="6 5" /></svg>unverified / speculative</span>
      <span className="flex items-center gap-1.5"><svg width="30" height="8"><line x1="0" y1="4" x2="30" y2="4" stroke="var(--color-line-strong)" strokeWidth="1.5" strokeDasharray="2 4" /></svg>captured at this place</span>
    </div>
  )
}

function MediaDetail({ m, edges, mById, caseId, blur, places }: { m: MNode; edges: MEdge[]; mById: Map<string, MNode>; caseId: string; blur: boolean; places: LNode[] }) {
  const mine = edges.filter((e) => e.a === m.id || e.b === m.id)
  return (
    <div className="grid gap-5 md:grid-cols-[200px_1fr]">
      <div>
        <SensitiveImg caseId={caseId} sha={m.thumb} blur={blur} className="h-48 w-full rounded bg-black" />
        <Link className="btn mt-2 w-full justify-center" to={casePath(caseId, `decomposition/${m.id}`)}>Open forensic breakdown</Link>
      </div>
      <div className="space-y-3 text-[13px]">
        <div className="text-ink-2">{m.width ? `${m.width}×${m.height} px` : m.modality} · {(m.mime || '').split('/')[1]?.toUpperCase()} · {fmtSize(m.size)}{m.time ? ` · earliest known ${fmtTime(m.time)}` : ''}</div>
        {m.verdict && <div className="flex items-center gap-2"><VerdictBadge v={m.verdict} size="md" /><span className="text-[12px] text-ink-3">{m.verdict.engine === 'hive' ? 'decided by Hive' : 'local detectors'}</span></div>}
        <div>
          <div className="label mb-1.5">How it relates to the other versions</div>
          {mine.length === 0 && <div className="text-ink-3">No links to other media were found.</div>}
          <div className="space-y-2">
            {mine.map((e) => {
              const other = mById.get(e.a === m.id ? e.b : e.a)
              return (
                <div key={e.id} className="rounded-lg border border-line p-3">
                  <div className="flex flex-wrap items-center gap-2"><Pill tone={e.status === 'Verified' ? 'good' : 'warn'}>{e.status}</Pill><span className="text-ink-2">with {other?.role === 'primary' ? 'the suspect media' : `version (${other?.width}×${other?.height} ${(other?.mime || '').split('/')[1]})`}</span>
                    {e.direction && <Pill>Inferred: {e.from === m.id ? 'this one was made from the other' : 'the other was made from this one'}</Pill>}</div>
                  <div className="mt-1 text-[12px] text-ink-3">{e.meaning}</div>
                  {e.differences.length > 0 && <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-[12.5px] text-ink-2">{e.differences.map((d) => <li key={d}>{d}</li>)}</ul>}
                  {e.notes.length > 0 && <div className="mt-1 text-[11.5px] text-ink-3">{e.notes.join(' ')}</div>}
                </div>
              )
            })}
          </div>
        </div>
        {places.length > 0 && <div><div className="label mb-1">Seen at</div><ul className="space-y-0.5 text-[12.5px] text-ink-2">{places.map((l) => <li key={l.id}>{l.platform || l.domain} — <span className="mono text-[11px] text-ink-3">{short(l.url, 70)}</span></li>)}</ul></div>}
      </div>
    </div>
  )
}

function PlaceDetail({ l, caseId, blur, onToggle }: { l: LNode; caseId: string; blur: boolean; onToggle: (l: LNode, d: boolean) => void }) {
  const r = REL[l.relation] ?? REL.pending
  return (
    <div className="grid gap-5 md:grid-cols-[240px_1fr]">
      <SensitiveImg caseId={caseId} sha={l.screenshot_sha} blur={blur} className="h-44 w-full rounded bg-black" />
      <div className="space-y-2 text-[13px]">
        <div className="mono break-all text-[12px] text-ink-2">{l.url}</div>
        <div className="flex flex-wrap items-center gap-2"><Pill tone={r.tone}>{r.label}</Pill><StateTag s={l.state} /><span className="text-ink-3">found by {VIA[l.discovered_via] ?? l.discovered_via}</span></div>
        <p className="text-ink-3">{l.relation_reason}</p>
        <div className="text-ink-2">Captured {fmtTime(l.first_captured_at)}{l.published_at ? ` · page declares it was published ${fmtTime(l.published_at)} (${l.published_source || 'source not recorded'})` : ' · no publication time shown on the page'}</div>
        {l.caption && <p className="rounded bg-bg/60 p-2 text-[12.5px] text-ink-2">“{l.caption}”</p>}
        <p className="text-[12px] text-ink-3">Appearing here does not mean the account that posted it made it.</p>
        <div className="flex gap-2">
          <a className="btn" href={l.url} target="_blank" rel="noreferrer"><ExternalLink size={13} /> Open the page</a>
          {!l.seed && <button className="btn" onClick={() => onToggle(l, true)}><EyeOff size={13} /> Not related</button>}
          <button className="btn" onClick={() => onToggle(l, false)}><Plus size={13} /> Keep in case record</button>
        </div>
      </div>
    </div>
  )
}
