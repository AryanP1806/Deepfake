/* eslint-disable @typescript-eslint/no-explicit-any */
import { Camera, ExternalLink, Globe2, Plus, Radar, RefreshCw, Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { api, fmtTime, vaultUrl, type Claim } from '../api'
import { ClaimList, Empty, Kv, NotChecked, PageHeader, Pill, Section, SensitiveImg, Spinner, StatusBadge } from '../components/ui'
import { useCase } from '../context'
import { useApi } from '../hooks'

interface Loc {
  id: string; url: string; final_url: string; domain: string; platform: string; account_handle: string; caption: string
  published_at?: string; published_source: string; first_captured_at?: string; last_checked_at?: string; state: string
  http_status?: number; screenshot_sha: string; html_sha: string; reach: Record<string, string>; discovered_via: string
  media: { id: string; thumb?: string; label: string; modality: string }[]; is_earliest_discovered: boolean; is_inferred_root: boolean
  notes: string; page_meta: any
}
interface Data { locations: Loc[]; enrichment: { wayback: Record<string, any>; domains: Record<string, any> }; claims: Claim[]; adapters: any[]; free_sources: any[] }

export default function Propagation() {
  const { caseId, data: cd, tick, toast, withConsent } = useCase()
  const { data, reload } = useApi<Data>(`/api/cases/${caseId}/propagation`, [tick])
  const { data: gen } = useApi<any>(`/api/cases/${caseId}/genealogy`, [tick])
  const [urls, setUrls] = useState('')
  const [busy, setBusy] = useState(false)
  const [sel, setSel] = useState<Loc | null>(null)

  const add = async () => {
    const items = urls.split(/\s+/).map((u) => u.trim()).filter(Boolean).map((url) => ({ url }))
    if (!items.length) return
    setBusy(true)
    try {
      const r = await api.post(`/api/cases/${caseId}/locations`, items)
      toast(`${r.added.length} URL(s) added — capture started`)
      setUrls('')
      reload()
    } catch (e) { toast((e as Error).message, 'err') } finally { setBusy(false) }
  }
  const search = async (a: any) => {
    const body: any = {}
    if (a.kind === 'text') body.query = window.prompt('Search text (e.g. caption, hashtag or transcript phrase):') ?? ''
    if (a.kind === 'channel') body.channel = window.prompt('Public channel username (without @):') ?? ''
    if (a.kind === 'image') body.media_id = cd.media.find((m) => m.role === 'primary')?.id
    const r = await withConsent(() => api.post(`/api/cases/${caseId}/adapters/${a.name}/search`, body))
    if (r?.not_run) toast(r.not_run.reason, 'err')
    else if (r) { toast(`${r.candidates} candidate(s); ${r.new_locations.length} new — pending capture`); reload() }
  }

  return (
    <div>
      <PageHeader step="Trace" title="Propagation"
        question="Where did the media appear, when, under which accounts and captions — and how did it change as it spread?"
        right={<button className="btn" onClick={async () => { await api.post(`/api/cases/${caseId}/capture`, null); toast('Capturing pending URLs') }}><Camera size={14} /> Capture pending</button>} />

      <div className="grid gap-5 xl:grid-cols-[1fr_340px]">
        <Section title="Propagation map" subtitle="One lane per platform, ordered by time. Arrows follow the inferred derivation between copies. Hover for details; click to open the capture.">
          {!data ? <Spinner /> : data.locations.length === 0 ? <Empty>No locations yet. Add the URLs where the media was seen.</Empty> :
            <PropagationMap locs={data.locations} gen={gen} caseId={caseId} blur={cd.blur} onSelect={setSel} />}
        </Section>
        <div className="space-y-5">
          <Section title="Add locations" subtitle="Paste every URL where the media was seen, one per line. Each is captured right away: screenshot, HTML, media, headers and hashes.">
            <textarea className="input mono min-h-28 text-[12px]" placeholder={'https://instagram.com/p/…\nhttps://t.me/s/channel/123\nhttps://example-forum.net/thread/…'} value={urls} onChange={(e) => setUrls(e.target.value)} />
            <button className="btn btn-primary mt-2 w-full justify-center" disabled={busy || !urls.trim()} onClick={add}><Plus size={14} /> Add & capture</button>
            <p className="mt-2 text-[11px] text-ink-3">Public pages only, fetched as an anonymous visitor. Private chats, logged-in content and dark-web sites are not accessed.</p>
          </Section>
          <Section title="Discovery sources" subtitle="Search results are only candidates. A result counts as evidence once it has been captured and matched.">
            <div className="space-y-2">
              {data?.free_sources.map((a) => (
                <div key={a.name} className="flex items-start gap-2 rounded-lg border border-line p-2.5 text-[12px]"><Globe2 size={14} className="mt-0.5 text-good" />
                  <div><div className="font-semibold text-ink-2">{a.title}</div><div className="text-ink-3">{a.notes || 'Enabled — runs automatically after capture.'}</div></div></div>
              ))}
              {data?.adapters.map((a) => a.enabled ? (
                <button key={a.name} className="btn w-full justify-start" onClick={() => search(a)}><Search size={13} /> {a.title}{a.consent_action && <Pill tone="warn">consent</Pill>}</button>
              ) : (
                <div key={a.name} className="flex items-center gap-2 rounded-md border border-dashed border-line-strong px-2.5 py-1.5 text-[12px]" title={`${a.reason}
${a.how_to_enable}`}>
                  <span className="h-2 w-2 shrink-0 rounded-full border border-ink-3" /><span className="flex-1 truncate text-ink-2">{a.title}</span>
                  <span className="shrink-0 text-[11px] text-ink-3">{a.reason.includes('retired') ? 'retired' : 'no key'}</span>
                </div>
              ))}
              <a href="/settings" className="block pt-1 text-[11px] text-ink-3 underline">Configure keys in Settings →</a>
            </div>
          </Section>
        </div>
      </div>

      <Section className="mt-5" title="Locations" subtitle="Captured as an anonymous visitor. Publish times are the times the site declares or displays.">
        <div className="overflow-x-auto">
          <table className="w-full text-[12.5px]">
            <thead><tr className="border-b border-line text-left text-ink-3">
              <th className="py-2 pr-3 font-medium">Capture</th><th className="pr-3 font-medium">Platform / account</th><th className="pr-3 font-medium">Published</th>
              <th className="pr-3 font-medium">Caption</th><th className="pr-3 font-medium">State</th><th className="font-medium">Media</th></tr></thead>
            <tbody>
              {data?.locations.map((l) => (
                <tr key={l.id} className="cursor-pointer border-b border-line/60 align-top hover:bg-raised/40" onClick={() => setSel(l)}>
                  <td className="py-2 pr-3"><SensitiveImg caseId={caseId} sha={l.screenshot_sha} blur={cd.blur} className="h-14 w-20 rounded bg-black" /></td>
                  <td className="pr-3"><div className="font-medium">{l.platform}</div><div className="text-ink-2">{l.account_handle ? `@${l.account_handle}` : l.domain}</div>
                    <div className="mt-1 flex gap-1">{l.is_earliest_discovered && <Pill>earliest found</Pill>}{l.is_inferred_root && <Pill>closest-to-source</Pill>}</div></td>
                  <td className="pr-3 text-ink-2">{fmtTime(l.published_at)}<div className="text-[11px] text-ink-3">{l.published_source || '—'}</div></td>
                  <td className="max-w-xs pr-3 text-ink-2">{l.caption || <span className="text-ink-3">—</span>}</td>
                  <td className="pr-3"><StateTag s={l.state} /><div className="text-[11px] text-ink-3">{l.http_status ? `HTTP ${l.http_status}` : ''}</div></td>
                  <td><div className="flex gap-1">{l.media.map((m) => <SensitiveImg key={m.id} caseId={caseId} sha={m.thumb} blur={cd.blur} className="h-10 w-10 rounded bg-black" />)}</div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <div className="mt-5 grid gap-5 lg:grid-cols-2">
        <Section title="Archive & domain intelligence" subtitle="Third-party public records. Hosting network describes the server/CDN — never the uploader’s location.">
          {data && <Enrichment e={data.enrichment} />}
        </Section>
        <Section title="Propagation findings"><ClaimList claims={data?.claims ?? []} /></Section>
      </div>
      {sel && <LocationDrawer l={sel} caseId={caseId} blur={cd.blur} wayback={data?.enrichment.wayback[sel.url]} onClose={() => setSel(null)} onRecapture={async () => { await api.post(`/api/cases/${caseId}/capture`, [sel.id]); toast('Re-capture queued') }} />}
    </div>
  )
}

export function StateTag({ s }: { s: string }) {
  const tone: Record<string, string> = { live: 'var(--color-good)', removed: 'var(--color-crit)', changed: 'var(--color-warn)', pending: 'var(--color-ink-3)', error: 'var(--color-crit)', restricted: 'var(--color-warn)' }
  return <span className="inline-flex items-center gap-1.5 text-[12px] font-semibold text-ink"><span className="h-2 w-2 rounded-full" style={{ background: tone[s] ?? 'var(--color-ink-3)' }} />{s}</span>
}

function PropagationMap({ locs, gen, caseId, blur, onSelect }: { locs: Loc[]; gen: any; caseId: string; blur: boolean; onSelect: (l: Loc) => void }) {
  const [hover, setHover] = useState<Loc | null>(null)
  const geo = useMemo(() => {
    const t = (l: Loc) => new Date(l.published_at || l.first_captured_at || Date.now()).getTime()
    const lanes = [...new Set([...locs].sort((a, b) => t(a) - t(b)).map((l) => l.platform))]
    const ts = locs.map(t)
    const t0 = Math.min(...ts), t1 = Math.max(...ts)
    const W = 900, laneH = 74, padL = 170, padR = 40, top = 30
    const x = (v: number) => padL + (t1 === t0 ? 0.5 : (v - t0) / (t1 - t0)) * (W - padL - padR)
    const pos: Record<string, { x: number; y: number }> = {}
    const laneCount: Record<string, number> = {}
    for (const l of [...locs].sort((a, b) => t(a) - t(b))) {
      const lane = lanes.indexOf(l.platform)
      const k = `${lane}`
      const n = (laneCount[k] = (laneCount[k] ?? 0) + 1)
      pos[l.id] = { x: x(t(l)), y: top + lane * laneH + laneH / 2 + (n % 2 === 0 ? 12 : -4) }
    }
    // links: genealogy edges mapped to locations carrying parent/child media
    // each media's first appearance carries its derivation links; later posts of identical bytes are reposts
    const mediaLoc: Record<string, string[]> = {}
    for (const l of [...locs].sort((a, b) => t(a) - t(b))) for (const m of l.media) (mediaLoc[m.id] ??= []).push(l.id)
    const links: { a: string; b: string; verified: boolean }[] = []
    for (const e of gen?.tree?.edges ?? []) {
      const a = mediaLoc[e.parent]?.[0], b = mediaLoc[e.child]?.[0]
      if (a && b && a !== b) links.push({ a, b, verified: e.verified_match })
    }
    // identical bytes posted at several locations
    for (const ids of Object.values(mediaLoc)) for (let i = 1; i < ids.length; i++) links.push({ a: ids[0], b: ids[i], verified: true })
    const days: number[] = []
    const day = 86400000
    for (let d = Math.ceil(t0 / day) * day; d <= t1; d += day) days.push(d)
    return { lanes, pos, links, W, H: top + lanes.length * laneH + 30, padL, x, days, laneH, top }
  }, [locs, gen])

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${geo.W} ${geo.H}`} className="w-full">
        <defs><marker id="parr" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0L10,5L0,10z" fill="var(--color-ink-3)" /></marker></defs>
        {geo.lanes.map((ln, i) => (
          <g key={ln}>
            <rect x={0} y={geo.top + i * geo.laneH} width={geo.W} height={geo.laneH} fill={i % 2 ? 'transparent' : 'color-mix(in srgb, var(--color-ink) 4%, transparent)'} />
            <text x={10} y={geo.top + i * geo.laneH + geo.laneH / 2 + 4} fontSize={12} fill="var(--color-ink-2)" fontWeight={600}>{ln}</text>
          </g>
        ))}
        {geo.days.map((d) => (
          <g key={d}>
            <line x1={geo.x(d)} x2={geo.x(d)} y1={geo.top - 6} y2={geo.H - 26} stroke="var(--color-line)" />
            <text x={geo.x(d)} y={geo.H - 10} fontSize={10} fill="var(--color-ink-3)" textAnchor="middle">{new Date(d).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', timeZone: 'Asia/Kolkata' })}</text>
          </g>
        ))}
        {geo.links.map((k, i) => {
          const a = geo.pos[k.a], b = geo.pos[k.b]
          if (!a || !b) return null
          const mx = (a.x + b.x) / 2
          return <path key={i} d={`M${a.x},${a.y} C${mx},${a.y} ${mx},${b.y} ${b.x - 20},${b.y}`} fill="none" stroke="var(--color-ink-3)" strokeWidth={1.5} strokeDasharray={k.verified ? undefined : '2 4'} markerEnd="url(#parr)" opacity={0.7} />
        })}
        {locs.map((l) => {
          const p = geo.pos[l.id]
          const removed = l.state === 'removed'
          return (
            <g key={l.id} className="cursor-pointer" onMouseEnter={() => setHover(l)} onMouseLeave={() => setHover(null)} onClick={() => onSelect(l)}>
              <circle cx={p.x} cy={p.y} r={21} fill="var(--color-surface)" stroke={removed ? 'var(--color-crit)' : l.is_earliest_discovered ? 'var(--color-ink)' : 'var(--color-line-strong)'} strokeWidth={l.is_earliest_discovered ? 3 : 2} strokeDasharray={removed ? '4 3' : undefined} />
              <clipPath id={`c-${l.id}`}><circle cx={p.x} cy={p.y} r={18} /></clipPath>
              {l.media[0]?.thumb && <image href={vaultUrl(caseId, l.media[0].thumb)} x={p.x - 18} y={p.y - 18} width={36} height={36} preserveAspectRatio="xMidYMid slice" clipPath={`url(#c-${l.id})`} style={{ filter: blur ? 'blur(4px)' : undefined }} />}
              {removed && <line x1={p.x - 15} y1={p.y + 15} x2={p.x + 15} y2={p.y - 15} stroke="var(--color-crit)" strokeWidth={2.5} />}
              <text x={p.x} y={p.y + 34} fontSize={10} fill="var(--color-ink-2)" textAnchor="middle">{l.account_handle ? `@${l.account_handle}` : l.domain}</text>
            </g>
          )
        })}
      </svg>
      {hover && (
        <div className="card pointer-events-none absolute right-3 top-3 w-72 p-3 text-[12px] shadow-xl">
          <div className="font-semibold">{hover.platform} · {hover.account_handle ? `@${hover.account_handle}` : hover.domain}</div>
          <div className="text-ink-3">{fmtTime(hover.published_at)} · <StateTag s={hover.state} /></div>
          {hover.caption && <div className="mt-1.5 text-ink-2">“{hover.caption}”</div>}
          {Object.keys(hover.reach ?? {}).length > 0 && <div className="mt-1 text-ink-3">{Object.entries(hover.reach).map(([k, v]) => `${v} ${k}`).join(' · ')} (as displayed)</div>}
          <div className="mono mt-1 truncate text-[10px] text-ink-3">{hover.url}</div>
        </div>
      )}
      <div className="mt-2 flex flex-wrap gap-4 text-[11px] text-ink-3">
        <span>◯ thick ring = earliest discovered</span><span className="text-crit">⊘ dashed red = removed</span><span>— verified derivation · ┈ similarity only</span>
      </div>
    </div>
  )
}

function Enrichment({ e }: { e: Data['enrichment'] }) {
  const doms = Object.entries(e.domains ?? {})
  const wb = Object.entries(e.wayback ?? {})
  return (
    <div className="space-y-4 text-[12.5px]">
      <div>
        <div className="label mb-1.5">Internet Archive</div>
        {wb.length === 0 && <div className="text-ink-3">Not yet queried.</div>}
        {wb.map(([url, v]) => Array.isArray(v)
          ? <div key={url} className="flex items-center gap-2 py-0.5"><StatusBadge status="Verified" compact /><span className="truncate">{v.length ? `${v.length} snapshot(s), earliest ${fmtTime(v[0].timestamp)}` : 'no snapshots'}</span><span className="mono truncate text-[10px] text-ink-3">{url}</span></div>
          : <div key={url} className="flex items-center gap-2 py-0.5 text-ink-3"><StatusBadge status="Unknown" compact /><span className="truncate">{v.reason}</span><span className="mono truncate text-[10px]">{url}</span></div>)}
      </div>
      <div>
        <div className="label mb-1.5">Domains</div>
        {doms.map(([d, v]) => (
          <div key={d} className="mb-2 rounded border border-line p-2">
            <div className="font-semibold">{d}</div>
            {v.reason ? <div className="text-ink-3">{v.reason}</div> : <>
              {v.rdap && <Kv k="Registered" v={`${fmtTime(v.rdap.registered)} via ${v.rdap.registrar ?? 'unknown'}`} />}
              {v.rdap?.abuse_contact && <Kv k="Abuse contact" v={v.rdap.abuse_contact} />}
              {v.asn?.map((a: any) => <Kv key={a.ip} k={`Hosting ${a.ip}`} v={a.as_name ? `AS${a.asn} ${a.as_name} (${a.country})` : a.error ?? '—'} />)}
            </>}
          </div>
        ))}
      </div>
    </div>
  )
}

function LocationDrawer({ l, caseId, blur, wayback, onClose, onRecapture }: { l: Loc; caseId: string; blur: boolean; wayback: any; onClose: () => void; onRecapture: () => void }) {
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/60" onClick={onClose}>
      <div className="h-full w-full max-w-3xl overflow-y-auto border-l border-line bg-surface p-6" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-4">
          <div><div className="label">{l.platform}</div><div className="mt-1 break-all font-semibold">{l.url}</div></div>
          <div className="flex gap-2">
            <button className="btn" onClick={onRecapture}><RefreshCw size={13} /> Re-capture</button>
            <a className="btn" href={l.url} target="_blank" rel="noreferrer"><ExternalLink size={13} /></a>
            <button className="btn" onClick={onClose}>Close</button>
          </div>
        </div>
        <div className="mt-4 grid gap-1">
          <Kv k="Account" v={l.account_handle ? `@${l.account_handle}` : '—'} />
          <Kv k="Published" v={`${fmtTime(l.published_at)} — ${l.published_source || 'unknown source'}`} />
          <Kv k="First captured" v={fmtTime(l.first_captured_at)} />
          <Kv k="Last checked" v={`${fmtTime(l.last_checked_at)} · ${l.state} ${l.http_status ? `(HTTP ${l.http_status})` : ''}`} />
          <Kv k="Discovered via" v={l.discovered_via} />
          <Kv k="Screenshot SHA-256" v={l.screenshot_sha || '— (not stored)'} mono />
          <Kv k="HTML SHA-256" v={l.html_sha || '—'} mono />
          {l.caption && <Kv k="Caption" v={l.caption} />}
          {wayback && Array.isArray(wayback) && wayback[0] && <Kv k="Archive" v={<a className="underline" href={wayback[0].snapshot_url} target="_blank" rel="noreferrer">earliest snapshot {fmtTime(wayback[0].timestamp)}</a>} />}
        </div>
        {l.screenshot_sha
          ? <div className="mt-4 overflow-hidden rounded-lg border border-line"><SensitiveImg caseId={caseId} sha={l.screenshot_sha} blur={blur} className="w-full" /></div>
          : <div className="mt-4"><NotChecked component="Screenshot" reason="Not stored (capture failed or blocked by the safety policy)." /></div>}
        <div className="mt-3 flex gap-2 text-xs">
          {l.html_sha && <a className="btn" href={vaultUrl(caseId, l.html_sha, true)}><Radar size={12} /> Download captured HTML</a>}
        </div>
      </div>
    </div>
  )
}
