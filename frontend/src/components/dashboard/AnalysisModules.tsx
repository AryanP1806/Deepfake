import { ArrowRight } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useApi } from '../../hooks'
import { MODULE_CARDS, TOOLS } from '../../lib/tools'

interface ModuleInfo { id: string; available: boolean; enabled: boolean; reason: string }

export function AnalysisModules() {
  const { data } = useApi<{ modules: ModuleInfo[] }>('/api/forensics/modules')
  const by = new Map((data?.modules ?? []).map((m) => [m.id, m]))
  return (
    <section aria-labelledby="mods-h">
      <div className="mb-3 flex items-center justify-between px-1">
        <h2 id="mods-h" className="display text-[19px] font-bold">Analysis Modules</h2>
        <Link to="/lab" className="inline-flex items-center gap-1 text-[13px] font-semibold text-accent-text hover:underline">Run all <ArrowRight size={14} aria-hidden /></Link>
      </div>
      <ul className="grid grid-cols-2 gap-4 md:grid-cols-3 2xl:grid-cols-6">
        {MODULE_CARDS.map((k) => {
          const t = TOOLS[k]
          const m = t.moduleId ? by.get(t.moduleId) : undefined
          const off = m && (!m.available || !m.enabled)
          return (
            <li key={k}>
              <Link to={`/tools/${k}`} className="tile flex h-full flex-col gap-3 p-4" aria-describedby={off ? `mod-${k}-note` : undefined}>
                <span className="icon-chip" style={{ '--chip': t.hue } as React.CSSProperties}><t.icon size={20} aria-hidden /></span>
                <span className="min-w-0">
                  <span className="block text-[14px] font-semibold leading-tight text-ink">{t.title}</span>
                  <span className="mt-0.5 block text-[12px] leading-snug text-ink-3">{t.short}</span>
                  {off && <span id={`mod-${k}-note`} className="badge mt-2 !text-[10.5px]" style={{ '--b': 'var(--color-v-uncertain)' } as React.CSSProperties}>{!m!.enabled ? 'Turned off' : 'Needs setup'}</span>}
                </span>
              </Link>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
