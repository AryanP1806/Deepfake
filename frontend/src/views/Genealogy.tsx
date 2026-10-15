/* eslint-disable @typescript-eslint/no-explicit-any */
import dagre from '@dagrejs/dagre'
import { Crown, History, Play, RefreshCw, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import { api, fmtTime, vaultUrl, type Claim } from '../api'
import { ClaimList, Empty, PageHeader, Section, SensitiveImg, Spinner, StatusBadge } from '../components/ui'
import { useCase } from '../context'
import { useApi } from '../hooks'

interface GNode { id: string; label: string; role: string; modality: string; thumb?: string; width?: number; height?: number; mime: string; size: number; sha256: string
  location?: { url: string; platform: string; handle: string; published_at?: string; captured_at?: string; state: string } | null
  other_locations?: { url: string; platform: string; handle: string; state: string }[] }
interface GEdge { parent: string; child: string; transforms: { op: string; [k: string]: any }[]; verified_match: boolean; match_tier: string; direction_confidence: 'Inferred' | 'Lead'; notes: string[]; geometry?: any; a_media?: string; scores?: any }
interface Data { nodes: GNode[]; tree: { families: { members: string[]; root: string; earliest_observed?: string }[]; edges: GEdge[] }; claims: Claim[] }

const OP_LABEL: Record<string, string> = {
  crop: 'crop', mirror: 'mirror', rotate: 'rotate', downscale: 'downscale', recompress: 'recompress', 're-encode': 're-encode',
  format_change: 'format', text_overlay_added: 'caption/text', localized_change: 'partial edit', border_or_caption_bar: 'border/caption bar',
  screen_capture_or_border: 'screen capture', 'crop+reframe': 'reframe', partial_reuse_or_trim: 'trim/partial', audio_replaced: 'audio swapped',
}
const NODE_W = 210, NODE_H = 214

export default function Genealogy() {
  const { caseId, data: cd, tick, toast } = useCase()
  const { data } = useApi<Data>(`/api/cases/${caseId}/genealogy`, [tick])
  const [sel, setSel] = useState<GEdge | null>(null)

  const nodesById = useMemo(() => Object.fromEntries((data?.nodes ?? []).map((n) => [n.id, n])), [data])
  const families = (data?.tree.families ?? []).filter((f) => f.members.length > 1)
  const singles = (data?.tree.families ?? []).filter((f) => f.members.length === 1)

  return (
    <div>
      <PageHeader step="Trace" title="Media genealogy"
        question="Which files are the same underlying media — even when cropped, mirrored, captioned, re-encoded or screen-recorded — and how did each version derive from another?"
        right={<button className="btn" onClick={async () => { await api.post(`/api/cases/${caseId}/genealogy/run`); toast('Re-computing genealogy') }}><RefreshCw size={14} /> Recompute</button>} />
      {!data && <Spinner label="Loading genealogy" />}
      {data && families.length === 0 && <Empty>No related copies established yet. Add URLs under Propagation or upload other versions; every file is compared automatically.</Empty>}
      {data && families.map((f, i) => (
        <Section key={i} className="mb-5" title={`Media family ${i + 1} · ${f.members.length} versions`}
          subtitle="Arrows run from the inferred ancestor to the derived copy. Solid = geometrically verified same content; dotted = similarity only. The direction of each arrow is inferred, not proven.">
          <FamilyTree caseId={caseId} blur={cd.blur} family={f} edges={data.tree.edges.filter((e) => f.members.includes(e.parent))} nodes={nodesById} onEdge={setSel} />
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <div className="flex gap-2 rounded-lg border border-line p-3 text-[13px] text-ink-2">
              <Crown size={16} className="shrink-0 text-ink" />
              <div><b className="text-ink">Inferred closest-to-source copy:</b> {nodesById[f.root]?.label}. It has the least degradation and the most content of the copies discovered. <i>It is not the proven original.</i></div>
            </div>
            <div className="flex gap-2 rounded-lg border border-line p-3 text-[13px] text-ink-2">
              <History size={16} className="shrink-0 text-ink" />
              <div><b className="text-ink">Earliest discovered:</b> {f.earliest_observed ? nodesById[f.earliest_observed]?.label : 'no dated copy'}. Earlier copies may exist that are private, deleted or not yet found.</div>
            </div>
          </div>
        </Section>
      ))}
      {singles.length > 0 && data && (
        <Section title="Unlinked media" subtitle="No match established with any other file. That may mean they are unrelated, or that the transformation defeated matching." className="mb-5">
          <div className="flex flex-wrap gap-3">{singles.map((f) => <NodeCard key={f.root} n={nodesById[f.root]} caseId={caseId} blur={cd.blur} />)}</div>
        </Section>
      )}
      {data && <Section title="Match findings" subtitle="One claim per matched pair, plus the origin inference."><ClaimList claims={data.claims} /></Section>}
      {sel && <PairDrawer e={sel} nodes={nodesById} caseId={caseId} blur={cd.blur} onClose={() => setSel(null)} />}
    </div>
  )
}

function FamilyTree({ family, edges, nodes, caseId, blur, onEdge }: { family: { members: string[]; root: string; earliest_observed?: string }; edges: GEdge[]; nodes: Record<string, GNode>; caseId: string; blur: boolean; onEdge: (e: GEdge) => void }) {
  const layout = useMemo(() => {
    const g = new dagre.graphlib.Graph()
    g.setGraph({ rankdir: 'TB', nodesep: 34, ranksep: 92, marginx: 10, marginy: 10 })
    g.setDefaultEdgeLabel(() => ({}))
    family.members.forEach((m) => g.setNode(m, { width: NODE_W, height: NODE_H }))
    edges.forEach((e) => g.setEdge(e.parent, e.child))
    dagre.layout(g)
    const gg = g.graph()
    return { g, w: (gg.width ?? 600) + 20, h: (gg.height ?? 400) + 20 }
  }, [family, edges])
  return (
    <div className="overflow-x-auto">
      <div className="relative mx-auto" style={{ width: layout.w, height: layout.h }}>
        <svg className="absolute inset-0" width={layout.w} height={layout.h}>
          <defs>
            <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--color-ink-2)" /></marker>
          </defs>
          {edges.map((e, i) => {
            const pts = layout.g.edge(e.parent, e.child)?.points ?? []
            if (!pts.length) return null
            const d = pts.map((p: any, j: number) => `${j ? 'L' : 'M'}${p.x},${p.y}`).join('')
            const mid = pts[Math.floor(pts.length / 2)]
            const ops = [...new Set(e.transforms.map((t) => OP_LABEL[t.op] ?? t.op))]
            return (
              <g key={i} className="cursor-pointer" onClick={() => onEdge(e)}>
                <path d={d} fill="none" stroke="transparent" strokeWidth={16} />
                <path d={d} fill="none" stroke={e.verified_match ? 'var(--color-st-verified)' : 'var(--color-st-lead)'} strokeWidth={2}
                  strokeDasharray={e.verified_match ? (e.direction_confidence === 'Lead' ? '7 4' : undefined) : '2 4'} markerEnd="url(#arr)" />
                <foreignObject x={mid.x - 95} y={mid.y - 13} width={190} height={28}>
                  <div className="flex flex-wrap justify-center gap-1">
                    {ops.slice(0, 4).map((o) => <span key={o} className="rounded border border-line-strong bg-bg px-1.5 py-[1px] text-[10px] font-medium text-ink-2">{o}</span>)}
                  </div>
                </foreignObject>
              </g>
            )
          })}
        </svg>
        {family.members.map((m) => {
          const p = layout.g.node(m)
          return (
            <div key={m} className="absolute" style={{ left: p.x - NODE_W / 2, top: p.y - NODE_H / 2 }}>
              <NodeCard n={nodes[m]} caseId={caseId} blur={blur} root={m === family.root} earliest={m === family.earliest_observed} />
            </div>
          )
        })}
      </div>
    </div>
  )
}

function NodeCard({ n, caseId, blur, root, earliest }: { n: GNode; caseId: string; blur: boolean; root?: boolean; earliest?: boolean }) {
  if (!n) return null
  const l = n.location
  return (
    <div className={`card overflow-hidden ${root ? 'ring-1 ring-ink-2' : ''}`} style={{ width: NODE_W, height: NODE_H }}>
      <div className="relative">
        <SensitiveImg caseId={caseId} sha={n.thumb} blur={blur} className="h-[104px] w-full bg-black" />
        <div className="absolute left-1.5 top-1.5 flex gap-1">
          {root && <span className="flex items-center gap-1 rounded bg-black/80 px-1.5 py-0.5 text-[9px] font-bold text-ink"><Crown size={9} /> CLOSEST-TO-SOURCE</span>}
          {earliest && <span className="rounded bg-black/80 px-1.5 py-0.5 text-[9px] font-bold text-ink">EARLIEST FOUND</span>}
        </div>
        {n.modality === 'video' && <Play size={14} className="absolute bottom-1.5 right-1.5 text-white" />}
      </div>
      <div className="space-y-0.5 p-2.5 text-[11px]">
        <div className="truncate font-semibold text-ink">{l ? `${l.platform}` : n.role === 'primary' ? 'Complainant’s copy' : n.label}</div>
        {l?.handle && <div className="truncate text-ink-2">@{l.handle}</div>}
        <div className="truncate text-ink-3">{l?.published_at ? `published ${fmtTime(l.published_at, false)}` : l?.captured_at ? `captured ${fmtTime(l.captured_at, false)}` : 'uploaded to case'}</div>
        {(n.other_locations?.length ?? 0) > 0 && <div className="truncate font-medium text-ink-2" title={n.other_locations!.map((o) => o.url).join(', ')}>+ identical bytes at {n.other_locations!.map((o) => (o.handle ? '@' + o.handle : o.platform)).join(', ')}</div>}
        <div className="flex items-center justify-between text-ink-3">
          <span>{n.width ? `${n.width}×${n.height}` : n.modality} · {n.mime.split('/')[1]}</span>
          {l && <span className={l.state === 'removed' ? 'font-semibold text-crit' : ''}>{l.state}</span>}
        </div>
      </div>
    </div>
  )
}

function PairDrawer({ e, nodes, caseId, blur, onClose }: { e: GEdge; nodes: Record<string, GNode>; caseId: string; blur: boolean; onClose: () => void }) {
  const parent = nodes[e.parent], child = nodes[e.child]
  const g = e.geometry
  const parentIsA = e.a_media === e.parent
  const region: number[] | undefined = g ? (parentIsA ? g.b_in_a : g.a_in_b) : undefined // child's footprint inside parent
  const changes = e.transforms.find((t) => t.op === 'localized_change')?.regions_in_a as { box: number[] }[] | undefined
  const Box = ({ b, color, dash }: { b: number[]; color: string; dash?: boolean }) => (
    <div className="pointer-events-none absolute" style={{ left: `${Math.max(0, b[0]) * 100}%`, top: `${Math.max(0, b[1]) * 100}%`, width: `${(Math.min(1, b[2]) - Math.max(0, b[0])) * 100}%`, height: `${(Math.min(1, b[3]) - Math.max(0, b[1])) * 100}%`, border: `2px ${dash ? 'dashed' : 'solid'} ${color}` }} />
  )
  const Img = ({ n, overlay }: { n: GNode; overlay?: React.ReactNode }) => (
    <div className="relative inline-block bg-black">
      {n.modality === 'image'
        ? <img src={vaultUrl(caseId, n.sha256)} className={`block max-h-[340px] max-w-full ${blur ? 'blur-sensitive' : ''}`} />
        : <SensitiveImg caseId={caseId} sha={n.thumb} blur={blur} className="h-[300px] w-[300px]" />}
      {n.modality === 'image' && overlay}
    </div>
  )
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/60" onClick={onClose}>
      <div className="h-full w-full max-w-4xl overflow-y-auto border-l border-line bg-surface p-6" onClick={(ev) => ev.stopPropagation()}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2"><StatusBadge status={e.verified_match ? 'Verified' : 'Lead'} /><span className="font-semibold">Same underlying media · direction</span><StatusBadge status={e.direction_confidence} /></div>
          <button className="btn" onClick={onClose}><X size={14} /></button>
        </div>
        <div className="mt-5 grid grid-cols-2 gap-4">
          <div><div className="label mb-1.5">Ancestor (inferred) — {parent.label}</div>
            <Img n={parent} overlay={<>{region && <Box b={region} color="var(--color-st-corroborated)" />}{parentIsA && changes?.map((c, i) => <Box key={i} b={c.box} color="var(--color-crit)" dash />)}</>} />
            <div className="mt-1 text-[11px] text-ink-3"><span style={{ color: 'var(--color-st-corroborated)' }}>■</span> area that appears in the derived copy {parentIsA && changes?.length ? <>· <span className="text-crit">▭</span> localised differences</> : null}</div>
          </div>
          <div><div className="label mb-1.5">Derived copy — {child.label}</div>
            <Img n={child} overlay={!parentIsA && changes?.map((c, i) => <Box key={i} b={c.box} color="var(--color-crit)" dash />)} />
          </div>
        </div>
        <div className="mt-6 grid gap-4 md:grid-cols-2">
          <div>
            <div className="label mb-2">Recovered transformations</div>
            <ul className="space-y-1.5 text-[13px]">
              {e.transforms.map((t, i) => (
                <li key={i} className="rounded border border-line px-2.5 py-1.5">
                  <span className="font-semibold">{OP_LABEL[t.op] ?? t.op}</span>
                  <span className="ml-2 text-ink-3">{Object.entries(t).filter(([k]) => !['op', 'regions_in_a', 'evidence'].includes(k)).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.map((x) => (typeof x === 'number' ? x.toFixed(2) : String(x))).join(', ') : v}`).join(' · ')}</span>
                  {t.evidence && <div className="text-[11px] text-ink-3">{t.evidence}</div>}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <div className="label mb-2">Match evidence</div>
            <div className="space-y-1 text-[13px] text-ink-2">
              <div>Tier: <b>{e.match_tier}</b> {e.verified_match ? '(geometrically verified)' : '(similarity only)'}</div>
              {g && <div>{g.inliers} aligned feature points, inlier ratio {(g.inlier_ratio * 100).toFixed(0)}% · scale {g.scale_b_to_a.toFixed(2)} · rotation {g.rotation_deg.toFixed(1)}°{g.mirrored ? ' · mirrored' : ''}</div>}
              {e.scores && <div className="mono text-[11px] text-ink-3">PDQ distance {e.scores.pdq ?? '—'} / mirrored {e.scores.pdq_mirror ?? '—'} (of 256 bits)</div>}
              {e.notes.map((n, i) => <div key={i} className="text-ink-3">• {n}</div>)}
              <div className="pt-2 text-[12px] text-ink-3">A verified match shows the two files share the same visual content. It does not show who made either copy. The direction is inferred from cropping, added material, degradation and observed times.</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
