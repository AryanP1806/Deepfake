/* eslint-disable @typescript-eslint/no-explicit-any */
import { ChevronRight, FileJson, Fingerprint, Layers, RefreshCw } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, fmtBytes, vaultUrl, type Claim } from '../api'
import { SeriesChart } from '../components/SeriesChart'
import { ClaimItem, Empty, Kv, LEVEL_STYLE, NotChecked, PageHeader, Section, SensitiveImg, Spinner, StatusBadge } from '../components/ui'
import { useCase } from '../context'
import { casePath } from '../lib/paths'
import { DetectorBars, VerdictBadge, VerdictCard, type Verdict } from '../components/VerdictCard'
import { useApi } from '../hooks'

interface Artifact { id: string; sha256: string; kind: string; label: string; mime: string; data: any }
interface Run { component: string; outcome: string; reason: string; how_to_enable: string; version: string }
interface Detail {
  media: any
  location: any
  claims: Claim[]
  artifacts: Artifact[]
  runs: Run[]
  fingerprint_summary: any
  explanation: {
    level: string; headline: string; caveat: string
    reasons: { text: string; claims: string[] }[]
    generator_families: { family: string; status: any; claims: string[] }[]
    not_checked: { component: string; reason: string; how_to_enable: string; outcome: string }[]
    verdict?: Verdict
  }
}

const OVERLAYS = ['trufor_map', 'content_box', 'ela', 'noise', 'faces'] as const
const OVERLAY_LABEL: Record<string, string> = { trufor_map: 'Where edited', content_box: 'Picture area', ela: 'ELA', noise: 'Noise', faces: 'Faces' }
const REPLACE_VIEW = new Set(['trufor_map', 'content_box'])  // full replacement images, not overlays
const CATEGORY_ORDER = ['c2pa', 'provenance', 'watermark', 'family_provenance', 'detector', 'metadata', 'encoding', 'faces', 'ela', 'noise', 'spectrum', 'jpeg_ghost', 'video', 'temporal', 'audio', 'asr', 'ocr']
const CATEGORY_LABEL: Record<string, string> = {
  c2pa: 'C2PA / Content Credentials', provenance: 'Generator provenance', watermark: 'Watermarks', family_provenance: 'Inherited from related copies',
  detector: 'Learned detectors', metadata: 'Metadata', encoding: 'Encoding', faces: 'Faces', ela: 'Error level analysis',
  noise: 'Sensor noise', spectrum: 'Frequency spectrum', jpeg_ghost: 'Compression history', video: 'Video', temporal: 'Temporal consistency',
  audio: 'Audio', asr: 'Speech', ocr: 'Burned-in text',
}

export default function Decomposition() {
  const { caseId, data, tick, refresh, withConsent, toast } = useCase()
  const { mediaId } = useParams()
  const nav = useNavigate()
  const current = mediaId ?? data.media.find((m) => m.role === 'primary')?.id ?? data.media[0]?.id
  const { data: d, loading } = useApi<Detail>(current ? `/api/cases/${caseId}/media/${current}` : null, [tick])
  const [overlay, setOverlay] = useState<string | null>(null)
  const [opacity, setOpacity] = useState(0.75)
  const [jsonView, setJsonView] = useState<{ label: string; body: string } | null>(null)
  const [series, setSeries] = useState<any>(null)

  const art = (k: string) => d?.artifacts.find((a) => a.kind === k)
  const ts = art('timeseries')
  useEffect(() => {
    setSeries(null)
    if (ts) fetch(vaultUrl(caseId, ts.sha256)).then((r) => r.json()).then(setSeries)
  }, [ts?.sha256, caseId]) // eslint-disable-line react-hooks/exhaustive-deps

  const grouped = useMemo(() => {
    const g: Record<string, Claim[]> = {}
    for (const c of d?.claims ?? []) if (!isAbsence(c)) (g[c.category] ??= []).push(c)
    const rank = (k: string) => (CATEGORY_ORDER.includes(k) ? CATEGORY_ORDER.indexOf(k) : 99)
    return Object.entries(g).sort((a, b) => rank(a[0]) - rank(b[0]))
  }, [d])
  const absent = (d?.claims ?? []).filter(isAbsence)

  const showJson = async (a: Artifact) => {
    const t = await fetch(vaultUrl(caseId, a.sha256)).then((r) => r.text())
    try { setJsonView({ label: a.label, body: JSON.stringify(JSON.parse(t), null, 2) }) } catch { setJsonView({ label: a.label, body: t }) }
  }
  const runApi = async (component: string) => {
    const action = { api_hive: 'api_detect:hive', api_sightengine: 'api_detect:sightengine', api_reality_defender: 'api_detect:reality_defender' }[component]
    if (!action) return
    const ok = window.confirm(`This uploads the media to a third-party service (${component.replace('api_', '')}). The upload is recorded in the custody log. Continue?`)
    if (!ok) return
    await withConsent(async () => {
      await api.post('/api/consent', { action, case_id: caseId, scope: 'once' })
      await api.post(`/api/cases/${caseId}/media/${current}/analyze?only=${component}`)
    })
    toast('Queued external detection')
  }

  if (!current) return <Empty>No media in this case yet. Add a file from the left panel.</Empty>

  return (
    <div>
      <PageHeader step="Detect · Explain" title="Forensic decomposition"
        question="What does the file itself reveal — and why is it suspicious? Each finding is evidence, not a verdict."
        right={<button className="btn" onClick={async () => { await api.post(`/api/cases/${caseId}/media/${current}/analyze`); toast('Re-analysis queued'); refresh() }}><RefreshCw size={14} /> Re-run analysis</button>} />

      <div className="mb-5 flex gap-2 overflow-x-auto pb-1">
        {data.media.map((m) => (
          <button key={m.id} onClick={() => nav(casePath(caseId, `decomposition/${m.id}`))}
            className={`card w-40 shrink-0 overflow-hidden text-left transition ${m.id === current ? 'border-ink-2' : 'hover:border-line-strong'}`}>
            <SensitiveImg caseId={caseId} sha={m.thumb} blur={data.blur} className="h-20 w-full bg-black" />
            <div className="p-2">
              <div className="truncate text-[11px] font-medium">{m.label}</div>
              <div className="mt-1 flex items-center justify-between"><span className="text-[10px] uppercase text-ink-3">{m.role} · {m.modality}</span>{(m as any).verdict ? <VerdictBadge v={(m as any).verdict} /> : m.level && <LevelDot level={m.level} />}</div>
            </div>
          </button>
        ))}
      </div>

      {(!d || loading) && !d && <Spinner label="Loading analysis" />}
      {d && (
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
          <div className="space-y-5">
            <Section title="Media" subtitle={`${d.media.modality} · ${d.media.mime} · ${fmtBytes(d.media.size)}${d.media.width ? ` · ${d.media.width}×${d.media.height}` : ''}${d.media.duration ? ` · ${d.media.duration.toFixed(1)} s` : ''}`}
              right={<div className="flex gap-1">
                {OVERLAYS.filter((o) => art(o)).map((o) => (
                  <button key={o} className={`btn whitespace-nowrap px-2 py-1 text-[11px] ${overlay === o ? 'btn-primary' : ''}`} onClick={() => setOverlay(overlay === o ? null : o)}><Layers size={12} />{OVERLAY_LABEL[o] ?? o}</button>
                ))}
              </div>}>
              {d.media.modality === 'image' && (
                <div className="relative overflow-hidden rounded-lg bg-black">
                  <SensitiveImg caseId={caseId} sha={overlay && REPLACE_VIEW.has(overlay) && art(overlay) ? art(overlay)!.sha256 : d.media.sha256} blur={data.blur} className="max-h-[520px] w-full" />
                  {overlay && !REPLACE_VIEW.has(overlay) && art(overlay) && (
                    <img src={vaultUrl(caseId, art(overlay)!.sha256)} className="pointer-events-none absolute inset-0 h-full w-full object-contain"
                      style={{ opacity, mixBlendMode: overlay === 'faces' ? 'normal' : 'screen', filter: data.blur ? 'blur(18px)' : undefined }} />
                  )}
                </div>
              )}
              {d.media.modality === 'video' && (
                <video src={vaultUrl(caseId, d.media.sha256)} controls className={`max-h-[460px] w-full rounded-lg bg-black ${data.blur ? 'blur-sensitive' : ''}`} />
              )}
              {d.media.modality === 'audio' && <audio src={vaultUrl(caseId, d.media.sha256)} controls className="w-full" />}
              {overlay && REPLACE_VIEW.has(overlay) && (
                <div className="mt-3 text-xs text-ink-3">{overlay === 'trufor_map' ? 'Brighter = regions the edit detector (TruFor) considers inconsistent with the rest of the picture.' : 'Blue box = the picture found inside the screenshot; detectors analysed only this area.'}</div>
              )}
              {overlay && !REPLACE_VIEW.has(overlay) && (
                <div className="mt-3 flex items-center gap-3 text-xs text-ink-3">
                  <span>{art(overlay)?.label}</span>
                  <input type="range" min={0} max={1} step={0.05} value={opacity} onChange={(e) => setOpacity(+e.target.value)} className="w-40" />
                  <span className="ml-auto">Bright areas = stronger response. Read with the claim's limitations.</span>
                </div>
              )}
              {art('frames') && <img src={vaultUrl(caseId, art('frames')!.sha256)} className={`mt-4 w-full rounded ${data.blur ? 'blur-sensitive' : ''}`} />}
              {series && <div className="mt-4"><SeriesChart series={series} /></div>}
              {art('spectrogram') && (
                <div className="mt-4">
                  <div className="label mb-1">Spectrogram · 0–22 kHz (bottom→top)</div>
                  <img src={vaultUrl(caseId, art('spectrogram')!.sha256)} className="h-40 w-full rounded object-fill" />
                </div>
              )}
              {art('fft') && (
                <div className="mt-4 flex items-start gap-4">
                  <img src={vaultUrl(caseId, art('fft')!.sha256)} className="h-36 w-36 rounded" />
                  <div className="text-xs text-ink-3"><div className="font-semibold text-ink-2">Residual frequency spectrum</div>Isolated bright dots away from the centre can indicate upsampling layers in generators. Peaks also come from resizing, JPEG blocks and screen moiré.</div>
                </div>
              )}
            </Section>

            <Section title="Identity & preservation" subtitle="Hashes identify this exact file in the vault and in the evidence package.">
              <Kv k="SHA-256" v={d.media.sha256} mono />
              <Kv k="MD5" v={d.media.md5} mono />
              <Kv k="Perceptual (PDQ)" v={d.fingerprint_summary.pdq ?? '—'} mono />
              {d.fingerprint_summary.encoder_signature && <Kv k="Encoder signature" v={d.fingerprint_summary.encoder_signature} mono />}
              {d.fingerprint_summary.jpeg_q && <Kv k="JPEG quality (est.)" v={d.fingerprint_summary.jpeg_q} />}
              {d.location && <Kv k="Captured from" v={<a className="underline" href={d.location.url} target="_blank" rel="noreferrer">{d.location.url}</a>} />}
              <div className="mt-3 flex flex-wrap gap-2">
                {d.artifacts.filter((a) => a.mime === 'application/json').map((a) => (
                  <button key={a.id} className="btn px-2 py-1 text-[11px]" onClick={() => showJson(a)}><FileJson size={12} /> {a.kind}</button>
                ))}
                <a className="btn px-2 py-1 text-[11px]" href={vaultUrl(caseId, d.media.sha256, true)}><Fingerprint size={12} /> Original bytes</a>
              </div>
            </Section>
          </div>

          <div className="space-y-5">
            <VerdictCard v={d.explanation.verdict} showWhere={overlay === 'trufor_map'}
              onShowWhere={art('trufor_map') ? () => setOverlay(overlay === 'trufor_map' ? null : 'trufor_map') : undefined} />
            <DetectorBars v={d.explanation.verdict} />
            {d.explanation.generator_families.length > 0 && (
              <div className="card p-4">
                <div className="label mb-2">Likely generation tool (from provenance data)</div>
                {d.explanation.generator_families.map((f) => (
                  <div key={f.family} className="flex items-center gap-2 text-sm"><StatusBadge status={f.status} /> {f.family}</div>
                ))}
              </div>
            )}
            <details className="card group overflow-hidden">
              <summary className="flex cursor-pointer list-none items-center justify-between px-5 py-4">
                <div><div className="font-semibold">Technical evidence for investigators</div>
                  <div className="text-[12.5px] text-ink-3">{d.claims.length} findings with status, reasoning and limits · {d.explanation.not_checked.length} tools not run</div></div>
                <ChevronRight size={16} className="text-ink-3 transition group-open:rotate-90" />
              </summary>
              <div className="space-y-5 border-t border-line p-5">
                {absent.length > 0 && (
                  <div className="rounded-lg border border-line bg-bg/40 p-3 text-[12.5px] text-ink-2">
                    <b className="text-ink">No provenance data in the file</b> ({absent.map((c) => c.category).join(', ')}). This is normal for images from social media and neither proves nor disproves anything.
                  </div>
                )}
            <Section title="Findings" subtitle="Grouped by technique. Expand any finding for its reasoning, limits and evidence." className="border-0 p-0">
              <div className="space-y-4">
                {grouped.map(([cat, cs]) => (
                  <div key={cat}>
                    <div className="label mb-1.5">{CATEGORY_LABEL[cat] ?? cat}</div>
                    <div className="space-y-1.5">{cs.map((c) => <ClaimItem key={c.id} c={c} />)}</div>
                  </div>
                ))}
              </div>
            </Section>

            <Section title="Not checked" subtitle="Unavailable tools are listed rather than hidden. Not checking something is not a negative result." className="border-0 p-0">
              <div className="space-y-2">
                {d.explanation.not_checked.length === 0 && <div className="text-sm text-ink-3">Every applicable analyzer ran.</div>}
                {d.explanation.not_checked.map((n) => (
                  <div key={n.component}>
                    <NotChecked component={n.component} reason={n.outcome === 'error' ? `Error: ${n.reason}` : n.reason} how={n.how_to_enable} />
                    {n.reason.includes('consent') && (
                      <button className="btn mt-1 text-[11px]" onClick={() => runApi(n.component)}>Grant consent & upload to {n.component.replace('api_', '')}</button>
                    )}
                  </div>
                ))}
                <Link to="/settings" className="text-xs text-ink-3 underline">Manage detectors, models and keys →</Link>
              </div>
            </Section>
              </div>
            </details>
          </div>
        </div>
      )}

      {jsonView && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/70 p-8" onClick={() => setJsonView(null)}>
          <div className="card max-h-full w-full max-w-3xl overflow-auto p-5" onClick={(e) => e.stopPropagation()}>
            <div className="mb-2 flex justify-between"><span className="font-semibold">{jsonView.label}</span><button className="btn" onClick={() => setJsonView(null)}>Close</button></div>
            <pre className="mono whitespace-pre-wrap text-[11px] text-ink-2">{jsonView.body}</pre>
          </div>
        </div>
      )}
    </div>
  )
}

function isAbsence(c: Claim) {
  return c.status === 'Unknown' && ['c2pa', 'provenance', 'watermark', 'metadata'].includes(c.category)
}

function LevelDot({ level }: { level: string }) {
  const s = LEVEL_STYLE[level] ?? LEVEL_STYLE.Inconclusive
  return <span className="flex items-center gap-1 text-[10px] font-semibold text-ink-2" title={s.label}><span className="h-2 w-2 rounded-full" style={{ background: s.color }} />{level}</span>
}
