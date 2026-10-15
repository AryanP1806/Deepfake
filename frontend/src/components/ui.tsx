import { AlertTriangle, ChevronDown, ChevronRight, CircleDashed, CircleDot, Eye, EyeOff, HelpCircle, Link2, ShieldCheck, Sparkles } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { vaultUrl, type Claim, type Status } from '../api'

export const STATUS: Record<Status, { color: string; dash: string; icon: typeof ShieldCheck; desc: string }> = {
  Verified: { color: 'var(--color-st-verified)', dash: '', icon: ShieldCheck, desc: 'Direct or cryptographic observation' },
  Corroborated: { color: 'var(--color-st-corroborated)', dash: '', icon: Link2, desc: 'Two or more independent sources agree' },
  Inferred: { color: 'var(--color-st-inferred)', dash: '6 4', icon: Sparkles, desc: 'Derived by reasoning from other evidence' },
  Lead: { color: 'var(--color-st-lead)', dash: '2 4', icon: CircleDot, desc: 'Technical similarity only — follow up, do not conclude' },
  Unknown: { color: 'var(--color-st-unknown)', dash: '1 6', icon: HelpCircle, desc: 'Explicit gap — not established' },
}
export const STATUS_ORDER: Status[] = ['Verified', 'Corroborated', 'Inferred', 'Lead', 'Unknown']

export function StatusBadge({ status, compact = false }: { status: Status; compact?: boolean }) {
  const s = STATUS[status] ?? STATUS.Unknown
  const Icon = s.icon
  return (
    <span
      title={`${status}: ${s.desc}`}
      className={`inline-flex h-fit shrink-0 items-center gap-1 self-start rounded-full border text-[11px] font-semibold leading-4 text-ink ${compact ? 'px-1 py-0.5' : 'px-2 py-0.5'} ${status === 'Unknown' ? 'hatch' : ''}`}
      style={{ borderColor: s.color, borderStyle: status === 'Inferred' ? 'dashed' : status === 'Lead' ? 'dotted' : 'solid', borderWidth: status === 'Corroborated' ? 3 : 1.5, borderLeftWidth: status === 'Corroborated' ? 3 : undefined }}
    >
      <Icon size={11} style={{ color: s.color }} />
      {!compact && status}
    </span>
  )
}

export function StatusLegend() {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-ink-2">
      {STATUS_ORDER.map((s) => (
        <span key={s} className="flex items-center gap-1.5" title={STATUS[s].desc}>
          <StatusBadge status={s} />
          <span className="hidden 2xl:inline text-ink-3">{STATUS[s].desc}</span>
        </span>
      ))}
    </div>
  )
}

export const LEVEL_STYLE: Record<string, { color: string; label: string }> = {
  Strong: { color: 'var(--color-crit)', label: 'Strong indicators' },
  Moderate: { color: '#ec835a', label: 'Moderate indicators' },
  Weak: { color: 'var(--color-warn)', label: 'Weak indicators' },
  Inconclusive: { color: 'var(--color-st-unknown)', label: 'Inconclusive' },
}

export function LevelBadge({ level, big = false }: { level?: string | null; big?: boolean }) {
  if (!level) return <span className="text-xs text-ink-3">not analysed</span>
  const s = LEVEL_STYLE[level] ?? LEVEL_STYLE.Inconclusive
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-md border font-semibold text-ink ${big ? 'px-3 py-1.5 text-sm' : 'px-2 py-0.5 text-[11px]'}`}
      style={{ borderColor: s.color, background: `color-mix(in srgb, ${s.color} 14%, transparent)` }}>
      <AlertTriangle size={big ? 15 : 11} style={{ color: s.color }} />
      {s.label}
    </span>
  )
}

export function NotChecked({ component, reason, how }: { component: string; reason: string; how?: string }) {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-dashed border-line-strong bg-raised/40 px-3 py-2 text-xs">
      <CircleDashed size={14} className="mt-0.5 shrink-0 text-ink-3" />
      <div>
        <span className="font-semibold text-ink-2">Not checked · {component}</span>
        <div className="text-ink-3">{reason}</div>
        {how && <div className="mono mt-0.5 text-[11px] text-ink-3">↳ {how}</div>}
      </div>
    </div>
  )
}

export function Section({ title, subtitle, right, children, className = '' }: { title: ReactNode; subtitle?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card p-5 ${className}`}>
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-[15px] font-semibold tracking-tight">{title}</h2>
          {subtitle && <p className="mt-0.5 text-[13px] text-ink-3">{subtitle}</p>}
        </div>
        {right}
      </div>
      {children}
    </section>
  )
}

export function PageHeader({ step, title, question, right }: { step: string; title: string; question: string; right?: ReactNode }) {
  return (
    <div className="mb-5 flex items-end justify-between gap-4">
      <div>
        <div className="label">{step}</div>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">{title}</h1>
        <p className="mt-1 text-ink-3">{question}</p>
      </div>
      {right}
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-line-strong p-6 text-center text-sm text-ink-3">{children}</div>
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-ink-3">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-ink-3 border-t-transparent" />
      {label}
    </div>
  )
}

export function SensitiveImg({ caseId, sha, blur, className = '', alt = '' }: { caseId: string; sha?: string | null; blur: boolean; className?: string; alt?: string }) {
  const [reveal, setReveal] = useState(false)
  if (!sha) return <div className={`flex items-center justify-center bg-raised text-xs text-ink-3 ${className}`}>no preview</div>
  return (
    <div className={`group relative overflow-hidden ${className}`}>
      <img src={vaultUrl(caseId, sha)} alt={alt} className={`h-full w-full object-contain ${blur && !reveal ? 'blur-sensitive' : ''}`} />
      {blur && (
        <span
          role="button"
          tabIndex={0}
          onClick={(e) => { e.preventDefault(); e.stopPropagation(); setReveal((r) => !r) }}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); setReveal((r) => !r) } }}
          className="absolute right-1.5 top-1.5 cursor-pointer rounded-md bg-black/70 p-1 text-ink opacity-80 hover:opacity-100"
          title={reveal ? 'Blur again' : 'Reveal (sensitive content)'}
        >
          {reveal ? <EyeOff size={13} /> : <Eye size={13} />}
        </span>
      )}
    </div>
  )
}

export function ClaimItem({ c, onEvidence }: { c: Claim; onEvidence?: (sha: string) => void }) {
  const [open, setOpen] = useState(false)
  const pol = c.polarity === 'supports_manipulation' ? '▲ supports manipulation' : c.polarity === 'supports_authentic' ? '▼ counter-evidence' : ''
  return (
    <div className="rounded-lg border border-line bg-bg/40">
      <button onClick={() => setOpen(!open)} className="flex w-full items-start gap-2.5 px-3 py-2.5 text-left">
        {open ? <ChevronDown size={14} className="mt-0.5 shrink-0 text-ink-3" /> : <ChevronRight size={14} className="mt-0.5 shrink-0 text-ink-3" />}
        <StatusBadge status={c.status} />
        <span className="flex-1 text-[13px] leading-snug text-ink">{c.statement}</span>
        {c.strength !== 'None' && <span className="shrink-0 text-[11px] text-ink-3">{c.strength}</span>}
      </button>
      {open && (
        <div className="space-y-2 border-t border-line px-9 py-3 text-[12.5px] text-ink-2">
          {pol && <div className="text-[11px] font-semibold text-ink-3">{pol}</div>}
          {c.why && <p><span className="label mr-2">Why</span>{c.why}</p>}
          {c.limitations && <p><span className="label mr-2">Limits</span>{c.limitations}</p>}
          {c.what_would_confirm && <p><span className="label mr-2">Would confirm</span>{c.what_would_confirm}</p>}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-ink-3">
            <span className="mono">{c.produced_by}</span>
            <span className="mono">{c.id}</span>
            {c.evidence_refs.slice(0, 4).map((r) => (
              <button key={r} className="mono underline decoration-dotted hover:text-ink" onClick={() => onEvidence?.(r)} title={r}>
                sha256:{r.slice(0, 10)}…
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export function ClaimList({ claims, empty = 'No findings.' }: { claims: Claim[]; empty?: string }) {
  if (!claims.length) return <Empty>{empty}</Empty>
  return <div className="space-y-1.5">{claims.map((c) => <ClaimItem key={c.id} c={c} />)}</div>
}

export function Kv({ k, v, mono = false }: { k: string; v: ReactNode; mono?: boolean }) {
  return (
    <div className="flex gap-3 py-1 text-[13px]">
      <span className="w-36 shrink-0 text-ink-3">{k}</span>
      <span className={`min-w-0 break-all text-ink-2 ${mono ? 'mono text-[12px]' : ''}`}>{v}</span>
    </div>
  )
}

export function Pill({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'crit' | 'warn' | 'good' }) {
  const c = { neutral: 'var(--color-line-strong)', crit: 'var(--color-crit)', warn: 'var(--color-warn)', good: 'var(--color-good)' }[tone]
  return <span className="inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium text-ink-2" style={{ borderColor: c }}>{children}</span>
}
