/* eslint-disable @typescript-eslint/no-explicit-any */
import cytoscape from 'cytoscape'
import { Maximize2 } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { vaultUrl, type Status } from '../api'
import { Kv, PageHeader, Section, Spinner, STATUS, StatusBadge } from '../components/ui'
import { useCase } from '../context'
import { useApi } from '../hooks'

const TYPE: Record<string, { label: string; shape: string; size: number }> = {
  media: { label: 'Media copy', shape: 'round-rectangle', size: 54 },
  url: { label: 'URL / post', shape: 'ellipse', size: 22 },
  account: { label: 'Account', shape: 'diamond', size: 34 },
  domain: { label: 'Domain', shape: 'hexagon', size: 28 },
  hashtag: { label: 'Hashtag', shape: 'tag', size: 20 },
  device: { label: 'Device metadata', shape: 'vee', size: 28 },
  encoder: { label: 'Encoder signature', shape: 'triangle', size: 22 },
  generator: { label: 'Generator signal', shape: 'star', size: 40 },
}
const REL_LABEL: Record<string, string> = {
  derived_copy: 'derived copy', found_at: 'found at', posted_by: 'posted by', hosted_on: 'hosted on', uses_hashtag: 'uses',
  handle_visible_in_media: 'handle in media', same_handle: 'same handle', similar_username: 'similar username',
  generator_signal: 'provenance', metadata_names_device: 'names device', encoder_signature: 'encoder', jpeg_table_family: 'JPEG tables',
}
const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim() || '#888'

export default function Graph() {
  const { caseId, data: cd, tick } = useCase()
  const { data } = useApi<any>(`/api/cases/${caseId}/graph`, [tick])
  const ref = useRef<HTMLDivElement>(null)
  const cyRef = useRef<cytoscape.Core | null>(null)
  const [sel, setSel] = useState<any>(null)
  const [hidden, setHidden] = useState<Set<string>>(new Set(['encoder']))

  const elements = useMemo(() => {
    if (!data) return []
    const nodes = data.nodes.filter((n: any) => !hidden.has(n.type)).map((n: any) => ({
      data: { ...n, id: n.id, label: n.type === 'url' ? (n.platform ?? '') : n.label, bg: n.thumb && !cd.blur ? vaultUrl(caseId, n.thumb) : undefined },
    }))
    const ids = new Set(nodes.map((n: any) => n.data.id))
    const edges = data.edges.filter((e: any) => ids.has(e.source) && ids.has(e.target)).map((e: any) => ({
      data: { ...e, id: e.id, label: REL_LABEL[e.rel] ?? e.rel },
    }))
    return [...nodes, ...edges]
  }, [data, hidden, caseId, cd.blur])

  useEffect(() => {
    if (!ref.current || !data) return
    const st = (s: Status) => STATUS[s] ?? STATUS.Unknown
    const cy = cytoscape({
      container: ref.current,
      elements,
            style: [
        { selector: 'node', style: {
          'background-color': '#2a2a27', 'border-color': '#5a5953', 'border-width': 1.5, label: 'data(label)', color: css('--color-ink-2'),
          'font-size': 9, 'text-valign': 'bottom', 'text-margin-y': 4, 'text-max-width': '110px', 'text-wrap': 'ellipsis', width: 24, height: 24,
        } },
        ...Object.entries(TYPE).map(([t, v]) => ({ selector: `node[type="${t}"]`, style: { shape: v.shape as any, width: v.size, height: v.size } })),
        { selector: 'node[type="media"]', style: { 'background-color': '#33322f', 'border-width': 2, 'border-color': '#c3c2b7' } },
        { selector: 'node[type="media"][bg]', style: { 'background-image': 'data(bg)', 'background-fit': 'cover' } },
        { selector: 'node[role="primary"]', style: { 'border-color': '#ffffff', 'border-width': 4, width: 70, height: 70 } },
        { selector: 'node[type="account"]', style: { 'background-color': '#3b3a36', 'border-color': '#f2f2ee' } },
        { selector: 'node[type="generator"]', style: { 'background-color': css('--color-crit'), 'border-color': '#fff' } },
        { selector: 'node[state="removed"]', style: { 'border-color': css('--color-crit'), 'border-style': 'dashed', 'border-width': 2.5 } },
        { selector: 'edge', style: {
          width: 1.6, 'curve-style': 'bezier', 'target-arrow-shape': 'triangle', 'arrow-scale': 0.7, label: 'data(label)', 'font-size': 7,
          color: css('--color-ink-3'), 'text-rotation': 'autorotate', 'text-background-color': css('--color-bg'), 'text-background-opacity': 0.85, 'text-background-padding': '1px',
        } },
        ...(['Verified', 'Corroborated', 'Inferred', 'Lead', 'Unknown'] as Status[]).map((s) => ({
          selector: `edge[status="${s}"]`,
          style: { 'line-color': st(s).color.startsWith('var') ? css(st(s).color.slice(4, -1)) : st(s).color, 'target-arrow-color': css(st(s).color.slice(4, -1)),
            'line-style': (s === 'Inferred' ? 'dashed' : s === 'Lead' || s === 'Unknown' ? 'dotted' : 'solid') as any, width: s === 'Corroborated' ? 3 : 1.6 },
        })),
        { selector: ':selected', style: { 'overlay-color': '#ffffff', 'overlay-opacity': 0.12, 'overlay-padding': 6 } },
        { selector: '.faded', style: { opacity: 0.15 } },
      ],
      layout: { name: 'cose', animate: false, nodeRepulsion: () => 26000, idealEdgeLength: () => 110, nodeOverlap: 20, gravity: 0.6, padding: 30, randomize: false, numIter: 2500 } as any,
    })
    cy.on('tap', 'node, edge', (ev) => {
      const el = ev.target
      setSel({ kind: el.isNode() ? 'node' : 'edge', ...el.data() })
      cy.elements().addClass('faded')
      el.closedNeighborhood().removeClass('faded')
      if (el.isEdge()) el.connectedNodes().removeClass('faded')
    })
    cy.on('tap', (ev) => { if (ev.target === cy) { cy.elements().removeClass('faded'); setSel(null) } })
    cyRef.current = cy
    return () => cy.destroy()
  }, [elements, data, cd.blur])

  const ranked = data?.analytics?.ranked_accounts ?? []
  const types = data ? [...new Set<string>(data.nodes.map((n: any) => n.type))] : []

  return (
    <div>
      <PageHeader step="Connect" title="Evidence graph"
        question="How are media, copies, accounts, usernames, domains, hashtags and technical signatures connected? Edges carry their evidence status." />
      {!data && <Spinner label="Building graph" />}
      {data && (
        <div className="grid gap-5 xl:grid-cols-[1fr_340px]">
          <div className="card relative overflow-hidden">
            <div className="absolute left-3 top-3 z-10 flex flex-wrap gap-1.5">
              {types.map((t) => (
                <button key={t} onClick={() => setHidden((h) => { const n = new Set(h); if (n.has(t)) n.delete(t); else n.add(t); return n })}
                  className={`rounded-md border px-2 py-1 text-[11px] font-medium ${hidden.has(t) ? 'border-line text-ink-3 line-through' : 'border-line-strong bg-bg/80 text-ink-2'}`}>
                  {TYPE[t]?.label ?? t} · {data.nodes.filter((n: any) => n.type === t).length}
                </button>
              ))}
            </div>
            <button className="btn absolute right-3 top-3 z-10 px-2" onClick={() => cyRef.current?.fit(undefined, 30)} title="Fit"><Maximize2 size={13} /></button>
            <div ref={ref} style={{ height: 640 }} />
            <div className="border-t border-line px-4 py-2 text-[11px] text-ink-3">Edges: solid = Verified · thick = Corroborated · dashed = Inferred · dotted = Lead/Unknown. Similar handles and shared signatures are leads, not identity.</div>
          </div>
          <div className="space-y-5">
            <Section title={sel ? (sel.kind === 'node' ? TYPE[sel.type]?.label ?? sel.type : 'Relationship') : 'Selection'}>
              {!sel && <div className="text-[13px] text-ink-3">Click a node or edge to see what connects it and how strongly.</div>}
              {sel?.kind === 'node' && <div>
                <div className="break-all font-semibold">{sel.type === 'url' ? data.nodes.find((n: any) => n.id === sel.id)?.label : sel.label}</div>
                {sel.platform && <Kv k="Platform" v={sel.platform} />}
                {sel.state && <Kv k="State" v={sel.state} />}
                {sel.level && <Kv k="Indicators" v={sel.level} />}
                {sel.published && <Kv k="Published" v={sel.published} />}
                {sel.centrality > 0 && <Kv k="Betweenness" v={sel.centrality.toFixed(3)} />}
                {sel.merged_into && <div className="mt-2 text-[12px] text-ink-3">Overlay handle folded into {sel.merged_into}.</div>}
              </div>}
              {sel?.kind === 'edge' && <div className="space-y-1.5 text-[13px]">
                <div className="flex items-center gap-2"><StatusBadge status={sel.status} /> <b>{REL_LABEL[sel.rel] ?? sel.rel}</b></div>
                {sel.transforms && <div className="text-ink-2">Transformations: {sel.transforms.join(', ') || '—'}</div>}
                {sel.direction && <div className="text-ink-2">Direction: {sel.direction}</div>}
                {sel.similarity && <div className="text-ink-2">Similarity: {sel.similarity}%</div>}
                {sel.note && <div className="text-ink-3">{sel.note}</div>}
              </div>}
            </Section>
            <Section title="Lead priority" subtitle="Ranked by position in the spread: posting the earliest or closest-to-source copy, handles burned into the media, and bridging position. This ranks where to look next, not who is responsible.">
              <ol className="space-y-2">
                {ranked.slice(0, 8).map((a: any, i: number) => (
                  <li key={a.node} className="rounded-lg border border-line p-2.5 text-[12.5px]">
                    <div className="flex justify-between"><span className="font-semibold">{i + 1}. {a.label}</span><span className="text-ink-3">{a.platform}</span></div>
                    {a.reasons.length > 0 && <div className="mt-0.5 text-ink-3">{a.reasons.join(' · ')}</div>}
                  </li>
                ))}
              </ol>
            </Section>
          </div>
        </div>
      )}
    </div>
  )
}
