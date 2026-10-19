/* eslint-disable @typescript-eslint/no-explicit-any */
import { AlertOctagon, CheckCircle2, HelpCircle, Info, ShieldAlert, ShieldCheck } from 'lucide-react'
import type { ReactNode } from 'react'

export interface Verdict {
  available: boolean
  label: 'fake' | 'likely_fake' | 'uncertain' | 'likely_real' | 'real' | 'not_analysed'
  headline: string
  kind?: string | null
  p_fake: number
  p_ai?: number | null
  p_edit?: number | null
  lean?: string
  confidence?: 'high' | 'medium' | 'low'
  calibrated?: boolean
  proof?: boolean
  reasons?: string[]
  caveats?: string[]
  reliability?: string | null
  detectors?: { name: string; checks: string; score: number; head: string; agrees: boolean | null; primary?: boolean }[]
  engine?: 'hive' | 'local'
  engine_note?: string
  second_opinion?: { engine: string; label: string; headline: string; p_fake: number; agrees: boolean; strong_disagreement: boolean } | null
  screenshot?: boolean
  summary?: string
  detail?: string
}

// Verdict colours are theme tokens (validated per mode); always paired with an icon + text label, never colour alone.
export const VERDICT_STYLE: Record<string, { color: string; icon: typeof ShieldAlert; short: string }> = {
  fake: { color: 'var(--color-v-fake)', icon: AlertOctagon, short: 'Fake' },
  likely_fake: { color: 'var(--color-v-likely-fake)', icon: ShieldAlert, short: 'Likely fake' },
  uncertain: { color: 'var(--color-v-uncertain)', icon: HelpCircle, short: 'Uncertain' },
  likely_real: { color: 'var(--color-v-likely-real)', icon: ShieldCheck, short: 'Likely real' },
  real: { color: 'var(--color-v-real)', icon: CheckCircle2, short: 'Real' },
  not_analysed: { color: 'var(--color-v-na)', icon: Info, short: 'Not analysed' },
}

/** Percent shown to people: never 0 or 100 unless there is provenance proof. */
export const shownPct = (p: number, proof?: boolean) => (proof ? Math.round(p * 100) : Math.min(99, Math.max(1, Math.round(p * 100))))

export function VerdictBadge({ v, size = 'sm' }: { v?: Pick<Verdict, 'label' | 'p_fake'> & { proof?: boolean } | null; size?: 'sm' | 'md' }) {
  if (!v) return <span className="text-[11px] text-ink-3">not analysed</span>
  const st = VERDICT_STYLE[v.label] ?? VERDICT_STYLE.not_analysed
  const Icon = st.icon
  const fakeSide = v.p_fake >= 0.5
  const pct = shownPct(fakeSide ? v.p_fake : 1 - v.p_fake, v.proof)
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-md border font-semibold text-ink ${size === 'md' ? 'px-2.5 py-1 text-[13px]' : 'px-2 py-0.5 text-[11px]'}`}
      style={{ borderColor: st.color, background: `color-mix(in srgb, ${st.color} 16%, transparent)` }}>
      <Icon size={size === 'md' ? 14 : 11} style={{ color: st.color }} />
      {st.short} · {pct}% {fakeSide ? 'fake' : 'real'}
    </span>
  )
}

function Meter({ p, proof }: { p: number; proof?: boolean }) {
  const zones = [
    { to: 15, label: 'Real', c: VERDICT_STYLE.real.color },
    { to: 35, label: 'Likely real', c: VERDICT_STYLE.likely_real.color },
    { to: 65, label: 'Uncertain', c: VERDICT_STYLE.uncertain.color },
    { to: 85, label: 'Likely fake', c: VERDICT_STYLE.likely_fake.color },
    { to: 100, label: 'Fake', c: VERDICT_STYLE.fake.color },
  ]
  const pct = shownPct(p, proof)
  let from = 0
  return (
    <div className="mt-4">
      <div className="relative h-3">
        <div className="flex h-3 gap-[2px] overflow-hidden rounded-full">
          {zones.map((z) => { const w = z.to - from; from = z.to; return <div key={z.label} style={{ width: `${w}%`, background: z.c, opacity: 0.85 }} /> })}
        </div>
        <div className="absolute top-1/2 h-6 w-6 -translate-x-1/2 -translate-y-1/2 rounded-full border-[3px] border-surface bg-ink" style={{ left: `${pct}%` }} />
      </div>
      <div className="mt-1.5 flex text-[10.5px] text-ink-3">
        {(() => { let f = 0; return zones.map((z) => { const w = z.to - f; f = z.to; return <span key={z.label} style={{ width: `${w}%` }} className="text-center">{z.label}</span> }) })()}
      </div>
    </div>
  )
}

function SubBar({ label, p, hint }: { label: string; p?: number | null; hint: string }) {
  if (p == null) return (
    <div className="flex items-center gap-3 text-[12.5px]"><span className="w-44 text-ink-3">{label}</span><span className="text-ink-3">not checked</span></div>
  )
  const pct = shownPct(p)
  const c = p >= 0.65 ? VERDICT_STYLE.likely_fake.color : p >= 0.35 ? VERDICT_STYLE.uncertain.color : VERDICT_STYLE.likely_real.color
  return (
    <div className="flex items-center gap-3 text-[12.5px]" title={hint}>
      <span className="w-44 text-ink-2">{label}</span>
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-raised"><div className="h-full rounded-full" style={{ width: `${pct}%`, background: c }} /></div>
      <span className="mono w-12 text-right text-ink">{pct}%</span>
    </div>
  )
}

export function VerdictCard({ v, right, onShowWhere, showWhere }: { v?: Verdict | null; right?: ReactNode; onShowWhere?: () => void; showWhere?: boolean }) {
  if (!v) return null
  if (!v.available) {
    return (
      <div className="card p-5">
        <div className="flex items-center gap-2 font-semibold"><Info size={16} /> {v.headline}</div>
        {v.detail && <p className="mt-1 text-[13px] text-ink-3">{v.detail}</p>}
      </div>
    )
  }
  const st = VERDICT_STYLE[v.label] ?? VERDICT_STYLE.uncertain
  const Icon = st.icon
  const fakeSide = v.p_fake >= 0.5
  return (
    <div className="card overflow-hidden" style={{ borderColor: st.color, borderWidth: 1.5 }} role="region" aria-label="Verdict">
      <div className="p-5" style={{ background: `color-mix(in srgb, ${st.color} 7%, transparent)` }}>
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="label">Verdict</div>
            <div className="mt-1 flex items-center gap-2.5">
              <Icon size={30} style={{ color: st.color }} />
              <span className="display text-[30px] font-semibold leading-tight">{v.headline}</span>
            </div>
            {v.kind && <div className="mt-1.5 text-[15px] font-medium text-ink-2">{v.kind}</div>}
          </div>
          <div className="text-right">
            <div className="text-[34px] font-bold leading-none" style={{ color: st.color }}>{shownPct(fakeSide ? v.p_fake : 1 - v.p_fake, v.proof)}%</div>
            <div className="text-[12px] font-semibold text-ink-2">likely {fakeSide ? 'fake' : 'real'}</div>
            <div className="mt-1 inline-flex items-center gap-1 rounded-full border border-line-strong px-2 py-0.5 text-[11px] text-ink-2">
              confidence: <b className="text-ink">{v.confidence}</b>
            </div>
          </div>
        </div>
        <p className="mt-3 text-[15px] leading-snug text-ink">{v.summary}</p>
        {v.engine_note && <EngineLine v={v} />}
        <Meter p={v.p_fake} proof={v.proof} />
      </div>
      <div className="space-y-2.5 border-t border-line p-5">
        <SubBar label="AI-generated image" p={v.p_ai} hint="Was the whole picture made by an AI image generator?" />
        <SubBar label="Face swap / edited" p={v.p_edit} hint="Was a face swapped in, or part of a real photo edited?" />
      </div>
      {!!v.reasons?.length && (
        <div className="border-t border-line p-5">
          <div className="label mb-2">Why</div>
          <ul className="space-y-1.5">{v.reasons.map((r) => <li key={r} className="flex gap-2 text-[13.5px] text-ink-2"><span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-ink-2" />{r}</li>)}</ul>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3 border-t border-line px-5 py-3 text-[12px] text-ink-3">
        {onShowWhere && <button className={`btn px-2.5 py-1 text-[12px] ${showWhere ? 'btn-primary' : ''}`} onClick={onShowWhere}>{showWhere ? 'Hide' : 'Show'} where</button>}
        {right}
        <span className="min-w-0 flex-1">{v.caveats?.join(' ')}</span>
      </div>
      {v.reliability && <div className="border-t border-line bg-bg/40 px-5 py-2.5 text-[12px] text-ink-3"><b className="text-ink-2">How reliable is this? </b>{v.reliability}</div>}
    </div>
  )
}

function EngineLine({ v }: { v: Verdict }) {
  const so = v.second_opinion
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-ink-2">
      <span className="rounded-full border border-line-strong px-2 py-0.5">{v.engine === 'hive' ? 'Decided by Hive' : 'Decided by local detectors'}</span>
      {so && (
        <span className={so.agrees ? '' : 'font-semibold text-warn'}>
          Second opinion (local): {so.headline} · {shownPct(so.p_fake >= 0.5 ? so.p_fake : 1 - so.p_fake)}% {so.p_fake >= 0.5 ? 'fake' : 'real'} — {so.agrees ? '✓ agrees' : '⚠ disagrees'}
        </span>
      )}
      {!so && v.engine === 'local' && v.engine_note?.includes('unavailable') && <span className="text-warn">Hive unavailable for this file</span>}
    </div>
  )
}

export function DetectorBars({ v }: { v?: Verdict | null }) {
  if (!v?.detectors?.length) return null
  return (
    <div className="card p-5">
      <div className="mb-1 font-semibold">What each detector says</div>
      <p className="mb-3 text-[12.5px] text-ink-3">Hive (commercial) decides the verdict when it is available; the local models are the second opinion. When they agree, the verdict is more trustworthy. Bars show each model's raw output (0 = real, 100 = fake).</p>
      <div className="space-y-2.5">
        {v.detectors.map((d) => {
          const pct = Math.round(d.score * 100)
          return (
            <div key={d.name} className="grid grid-cols-[150px_1fr_52px_86px] items-center gap-3 text-[12.5px]">
              <div><div className="font-semibold text-ink">{d.name}{d.primary && <span className="ml-1.5 rounded border border-line-strong px-1 text-[9.5px] font-medium uppercase tracking-wide text-ink-2">primary</span>}</div><div className="text-[11px] text-ink-3">{d.checks}</div></div>
              <div className="relative h-2 rounded-full bg-raised">
                <div className="absolute left-1/2 top-[-3px] h-[14px] w-px bg-line-strong" />
                <div className="h-full rounded-full" style={{ width: `${Math.max(2, pct)}%`, background: d.score >= 0.5 ? VERDICT_STYLE.likely_fake.color : VERDICT_STYLE.likely_real.color }} />
              </div>
              <span className="mono text-right text-ink">{pct}</span>
              <span className={`text-[11px] ${d.agrees === false ? 'font-semibold text-warn' : 'text-ink-2'}`} title={d.agrees === null ? 'Checks for edits to a real photo — not applicable when the whole picture is AI-generated' : ''}>{d.agrees === null ? '— n/a here' : d.agrees ? '✓ agrees' : '⚠ disagrees'}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
