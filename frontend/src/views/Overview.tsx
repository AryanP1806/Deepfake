import { ArrowRight, Compass } from 'lucide-react'
import { Link } from 'react-router-dom'
import { PageHeader, SensitiveImg, Spinner } from '../components/ui'
import { useCase } from '../context'
import { casePath } from '../lib/paths'
import { VerdictCard } from '../components/VerdictCard'
import { useApi } from '../hooks'

interface Item { text: string; refs: string[] }
interface Overview {
  narrative: { title: string; sections: { key: string; title: string; items: Item[] }[] }
  next_action: { primary: { title: string; why: string; action: string; link?: string }; dont: string[]; top_gaps: { id: string; question: string; next_evidence: string; who: string }[] }
}

const SECTION_LINK: Record<string, string> = {
  what: 'decomposition', why: 'decomposition', spread: 'propagation', connected: 'leads', proven: 'evidence',
  leads: 'graph', missing: 'gaps', next: 'act',
}
const SECTION_ACCENT: Record<string, string> = {
  proven: 'var(--color-st-verified)', leads: 'var(--color-st-lead)', missing: 'var(--color-st-unknown)',
}

export default function OverviewView() {
  const { caseId, data, tick } = useCase()
  const { data: ov } = useApi<Overview>(`/api/cases/${caseId}/overview`, [tick])
  const primary = data.media.filter((m) => m.role === 'primary')

  return (
    <div className="max-w-6xl">
      <PageHeader step="Case overview" title="What happened, and how sure are we?"
        question="Every line below is grounded in recorded findings. Click a section to see the evidence behind it." />

      {primary.map((m) => (
        <div key={m.id} className="mb-5 grid gap-5 lg:grid-cols-[320px_1fr]">
          <Link to={casePath(caseId, `decomposition/${m.id}`)} className="card block h-fit overflow-hidden transition hover:border-line-strong">
            <SensitiveImg caseId={caseId} sha={m.thumb} blur={data.blur} className="h-64 w-full bg-black" />
            <div className="space-y-1 p-4">
              <div className="truncate text-sm font-medium">{m.label}</div>
              <div className="text-[12px] text-ink-3">Open the full forensic breakdown →</div>
              <div className="mono truncate text-[10px] text-ink-3">sha256 {m.sha256}</div>
            </div>
          </Link>
          {m.verdict ? <VerdictCard v={m.verdict} /> : <div className="card p-6 text-sm text-ink-3">Analysis has not finished yet — the verdict appears here as soon as the detectors are done.</div>}
        </div>
      ))}

      <div className="grid gap-5 lg:grid-cols-[320px_1fr]">
        <div className="space-y-4">
          {!primary.length && <div className="card p-5 text-sm text-ink-3">Add the suspicious media file from the left panel to begin.</div>}
          {ov && (
            <div className="card border-ink/30 p-4">
              <div className="label flex items-center gap-1.5"><Compass size={12} /> What you should do now</div>
              <div className="mt-2 font-semibold leading-snug">{ov.next_action.primary.title}</div>
              <p className="mt-1.5 text-[13px] text-ink-2">{ov.next_action.primary.why}</p>
              <Link to={casePath(caseId, 'act')} className="btn btn-primary mt-3 w-full justify-center">Open action plan <ArrowRight size={14} /></Link>
              {ov.next_action.dont.length > 0 && (
                <ul className="mt-3 space-y-1 text-[12px] text-ink-3">{ov.next_action.dont.map((d) => <li key={d}>✕ {d}</li>)}</ul>
              )}
            </div>
          )}
        </div>

        <div className="space-y-3">
          {!ov && <Spinner label="Assembling case narrative" />}
          {ov?.narrative.sections.map((s, i) => (
            <Link key={s.key} to={casePath(caseId, SECTION_LINK[s.key] ?? 'overview')}
              className="card group block p-5 transition hover:border-line-strong"
              style={SECTION_ACCENT[s.key] ? { borderLeft: `3px solid ${SECTION_ACCENT[s.key]}` } : undefined}>
              <div className="flex items-center justify-between">
                <div className="flex items-baseline gap-3">
                  <span className="mono text-[11px] text-ink-3">{String(i + 1).padStart(2, '0')}</span>
                  <h3 className="font-semibold">{s.title}</h3>
                </div>
                <ArrowRight size={14} className="text-ink-3 opacity-0 transition group-hover:opacity-100" />
              </div>
              <ul className="mt-2.5 space-y-1.5 pl-8">
                {s.items.length === 0 && <li className="text-[13px] text-ink-3">Nothing recorded yet.</li>}
                {s.items.slice(0, 6).map((it, j) => (
                  <li key={j} className="text-[13px] leading-relaxed text-ink-2">{it.text}</li>
                ))}
                {s.items.length > 6 && <li className="text-[12px] text-ink-3">+ {s.items.length - 6} more</li>}
              </ul>
            </Link>
          ))}
        </div>
      </div>
    </div>
  )
}
