/* eslint-disable @typescript-eslint/no-explicit-any */
import { Check, CheckCircle2, CircleDashed, KeyRound, Puzzle, RotateCcw } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api'
import { Section, Spinner, StatusBadge } from '../components/ui'
import { useApi } from '../hooks'
import { usePrefs } from '../prefs'
import { ACCENTS, deriveAccent, HEX, THEMES, type ModeChoice, type MotionChoice, type ThemeId } from '../lib/theme'

type Tab = 'appearance' | 'forensics' | 'detectors' | 'keys'
const TABS: { id: Tab; label: string }[] = [
  { id: 'appearance', label: 'Appearance' }, { id: 'forensics', label: 'Forensic modules' },
  { id: 'detectors', label: 'Detectors & models' }, { id: 'keys', label: 'API keys' },
]

export default function Settings() {
  const [tab, setTab] = useState<Tab>((new URLSearchParams(window.location.search).get('tab') as Tab) || 'appearance')
  return (
    <div className="mx-auto max-w-5xl">
      <h1 className="display px-1 text-[28px] font-extrabold leading-tight">Settings</h1>
      <div role="tablist" aria-label="Settings sections" className="mt-4 flex gap-1 border-b border-line">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} id={`tab-${t.id}`} aria-controls={`panel-${t.id}`}
            onClick={() => { setTab(t.id); history.replaceState(null, '', `?tab=${t.id}`) }}
            className={`-mb-px border-b-2 px-4 py-2.5 text-[13.5px] font-medium ${tab === t.id ? 'border-accent text-ink' : 'border-transparent text-ink-3 hover:text-ink'}`}>{t.label}</button>
        ))}
      </div>
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="mt-6">
        {tab === 'appearance' && <Appearance />}
        {tab === 'forensics' && <ForensicModules />}
        {tab === 'detectors' && <Detectors />}
        {tab === 'keys' && <Keys />}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------------------------------------ appearance
function Segmented<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: { id: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div role="radiogroup" aria-label={label} className="seg">
      {options.map((o) => <button key={o.id} role="radio" aria-checked={value === o.id} onClick={() => onChange(o.id)}>{o.label}</button>)}
    </div>
  )
}

function ThemePreview({ theme, mode, accent }: { theme: ThemeId; mode: 'light' | 'dark'; accent: string }) {
  const a = deriveAccent(accent, theme, mode)
  const style = { '--color-accent': a.fill, '--color-accent-ink': a.ink, '--color-accent-text': a.text, '--color-accent-soft': a.soft } as React.CSSProperties
  return (
    <div data-theme={theme} data-mode={mode} style={style} className="overflow-hidden rounded-[10px] bg-bg p-3 text-ink" aria-hidden>
      <div className="flex gap-2">
        <div className="w-10 space-y-1.5 rounded bg-surface p-1.5"><i className="block h-1.5 rounded-full bg-accent" /><i className="block h-1.5 rounded-full bg-line-strong" /><i className="block h-1.5 rounded-full bg-line-strong" /></div>
        <div className="card flex-1 space-y-2 p-2.5">
          <div className="display text-[13px] font-semibold leading-none">Is it fake?</div>
          <div className="meter"><i style={{ width: '62%' }} /></div>
          <div className="flex items-center gap-1.5">
            <span className="btn btn-primary !min-h-0 !px-2 !py-0.5 text-[10px]">Run</span>
            <span className="btn !min-h-0 !px-2 !py-0.5 text-[10px]">Export</span>
            <span className="tag text-[9px]">Lead</span>
          </div>
        </div>
      </div>
    </div>
  )
}

function Appearance() {
  const { prefs, mode, accent, set, reset, saveState } = usePrefs()
  const [hex, setHex] = useState(prefs.accent)
  const valid = HEX.test(hex)
  const changeAccent = (h: string) => { setHex(h); if (HEX.test(h)) set({ accent: h }) }
  return (
    <div className="space-y-5">
      <Section title="Theme" subtitle="Three looks for the same workbench. Every theme has a light and a dark mode. Charts, controls, cards, navigation and status indicators all follow the choice.">
        <div role="radiogroup" aria-label="Theme" className="grid gap-4 md:grid-cols-3">
          {THEMES.map((t) => (
            <button key={t.id} role="radio" aria-checked={prefs.theme === t.id} onClick={() => set({ theme: t.id })}
              className={`rounded-xl border-2 p-2 text-left transition ${prefs.theme === t.id ? 'border-accent-text' : 'border-transparent hover:border-line-strong'}`}>
              <ThemePreview theme={t.id} mode={mode} accent={prefs.accent} />
              <div className="mt-2 flex items-center gap-2 px-1 font-semibold">{t.name}{prefs.theme === t.id && <Check size={14} className="text-accent-text" aria-label="selected" />}</div>
              <p className="px-1 pb-1 text-[12.5px] text-ink-3">{t.blurb}</p>
            </button>
          ))}
        </div>
      </Section>

      <Section title="Mode and accent">
        <div className="grid gap-6 md:grid-cols-2">
          <div>
            <div className="label mb-2">Mode</div>
            <Segmented<ModeChoice> label="Mode" value={prefs.mode} onChange={(m) => set({ mode: m })}
              options={[{ id: 'light', label: 'Light' }, { id: 'dark', label: 'Dark' }, { id: 'system', label: 'Match system' }]} />
            <p className="mt-2 text-[12.5px] text-ink-3">Currently showing {mode} mode{prefs.mode === 'system' ? ' because your system is set that way' : ''}.</p>
            <div className="label mb-2 mt-5">Motion</div>
            <Segmented<MotionChoice> label="Motion" value={prefs.motion} onChange={(m) => set({ motion: m })}
              options={[{ id: 'system', label: 'Match system' }, { id: 'reduce', label: 'Reduce' }, { id: 'full', label: 'Full' }]} />
            <p className="mt-2 text-[12.5px] text-ink-3">“Reduce” removes transitions and spinning indicators. “Match system” follows your operating system's reduced-motion setting.</p>
          </div>
          <div>
            <div className="label mb-2">Accent colour</div>
            <div role="radiogroup" aria-label="Accent colour" className="flex flex-wrap gap-2">
              {ACCENTS.map((a) => (
                <button key={a.hex} role="radio" aria-checked={prefs.accent.toLowerCase() === a.hex} aria-label={a.name} title={a.name} onClick={() => changeAccent(a.hex)}
                  className="flex h-9 w-9 items-center justify-center rounded-full border-2 border-line-strong" style={{ background: a.hex }}>
                  {prefs.accent.toLowerCase() === a.hex && <Check size={16} color={deriveAccent(a.hex, prefs.theme, mode).ink} />}
                </button>
              ))}
            </div>
            <div className="mt-3 flex items-center gap-2">
              <label className="sr-only" htmlFor="accent-hex">Custom accent colour (hex)</label>
              <input id="accent-hex" className={`input mono !w-32 ${valid ? '' : '!border-crit'}`} value={hex} onChange={(e) => changeAccent(e.target.value.startsWith('#') ? e.target.value : '#' + e.target.value)} maxLength={7} spellCheck={false} />
              <input type="color" aria-label="Pick a custom accent colour" value={valid ? hex : prefs.accent} onChange={(e) => changeAccent(e.target.value)} className="h-9 w-12 cursor-pointer rounded border border-line-strong bg-transparent p-0.5" />
            </div>
            {!valid && <p className="mt-1 text-[12px] text-crit">Use six hex digits, like #2f6f9f.</p>}
            <p className="mt-2 text-[12.5px] text-ink-3">
              Button text on the accent: {accent.fillContrast.toFixed(1)}:1 · accent text on the page: {accent.textContrast.toFixed(1)}:1 (needs 4.5:1).
              {accent.fill.toLowerCase() !== prefs.accent.toLowerCase() || accent.text.toLowerCase() !== prefs.accent.toLowerCase() ? ' The colour is nudged lighter or darker where needed so text stays readable.' : ''}
            </p>
          </div>
        </div>
      </Section>

      <Section title="Preview" subtitle="Live sample of the controls and indicators in the current theme.">
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <button className="btn btn-primary">Primary action</button>
            <button className="btn">Secondary</button>
            <button className="btn btn-quiet">Quiet</button>
            <button className="btn" disabled>Disabled</button>
            <input className="input !w-56" placeholder="Text field" aria-label="Sample text field" />
            <span className="tag">Tag</span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {(['Verified', 'Corroborated', 'Inferred', 'Lead', 'Unknown'] as const).map((s) => <StatusBadge key={s} status={s} />)}
          </div>
          <div className="meter max-w-sm" role="img" aria-label="Sample progress meter at 62 percent"><i style={{ width: '62%' }} /></div>
        </div>
      </Section>

      <Section title="Profile" subtitle="Shown at the bottom of the sidebar. Stored on this computer only.">
        <div id="profile" className="grid gap-4 sm:grid-cols-2">
          <div><label htmlFor="pf-name" className="label mb-1.5 block">Your name</label>
            <input id="pf-name" className="input" maxLength={60} value={prefs.profile?.name ?? ''} onChange={(e) => set({ profile: { name: e.target.value, role: prefs.profile?.role ?? 'Investigator' } })} placeholder="Optional" autoComplete="name" /></div>
          <div><label htmlFor="pf-role" className="label mb-1.5 block">Role</label>
            <input id="pf-role" className="input" maxLength={40} value={prefs.profile?.role ?? ''} onChange={(e) => set({ profile: { name: prefs.profile?.name ?? '', role: e.target.value } })} placeholder="Investigator" /></div>
        </div>
      </Section>

      <div className="flex items-center justify-between text-[12.5px] text-ink-3">
        <span role="status">
          {saveState === 'saving' ? 'Saving…' : saveState === 'saved' ? 'Saved. Your choices are kept in the data folder and in this browser, so they survive restarts.' : 'Saved in this browser only: the backend could not be reached.'}
        </span>
        <button className="btn btn-quiet" onClick={() => { reset(); setHex('#2f6fe0') }}><RotateCcw size={13} /> Reset appearance</button>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------------------------------------ forensic modules
const TIER: Record<string, { label: string; tone: string }> = {
  real: { label: 'Validated on real labelled data', tone: 'var(--color-good)' },
  synthetic: { label: 'Validated on simulated data only', tone: 'var(--color-warn)' },
  none: { label: 'Not validated against ground truth', tone: 'var(--color-ink-3)' },
}

function ForensicModules() {
  const { data, reload, error } = useApi<any>('/api/forensics/modules')
  if (error) return <div className="card p-5 text-crit">{error}</div>
  if (!data) return <Spinner label="Loading modules" />
  const toggle = async (id: string, enabled: boolean) => { await api.put(`/api/forensics/modules/${id}`, { enabled }); reload() }
  return (
    <div className="space-y-5">
      <Section title="Installed modules" subtitle="Each module answers one question and always reports findings, confidence, limitations, versions and evidence. Missing evidence is shown as Unknown, never as a clean result.">
        <div className="divide-y divide-line">
          {data.modules.map((m: any) => {
            const t = TIER[m.validation?.tier ?? 'none']
            return (
              <div key={m.id} className="py-4 first:pt-0 last:pb-0">
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold">{m.title}</span>
                      <span className="mono text-[11px] text-ink-3">v{m.version}</span>
                      <span className="tag">{m.origin}</span>
                      <span className="tag" style={{ borderColor: t.tone }}>{t.label}</span>
                    </div>
                    <p className="mt-1 text-[13px] text-ink-2">{m.question}</p>
                    <div className="mt-1 text-[12px] text-ink-3">Works on: {m.modalities.join(', ')}</div>
                    {!m.available && <div className="mt-2 rounded-md border border-warn/50 bg-warn/5 px-3 py-2 text-[12.5px] text-ink-2">Not available: {m.reason}</div>}
                    <ul className="mt-2 space-y-1">
                      {m.requirements.map((r: any) => (
                        <li key={r.name} className="flex flex-wrap items-center gap-2 text-[12px] text-ink-3">
                          {r.ok ? <CheckCircle2 size={13} className="text-good" aria-label="installed" /> : <CircleDashed size={13} aria-label="missing" />}
                          <span>{r.kind} <b className="text-ink-2">{r.name}</b>{r.optional ? ' (optional)' : ''}{r.version ? ` ${r.version}` : ''}</span>
                          {!r.ok && r.how_to_enable && <code className="mono rounded bg-raised px-1.5 py-0.5 text-[11px]">{r.how_to_enable}</code>}
                        </li>
                      ))}
                    </ul>
                    <details className="mt-2 text-[12.5px] text-ink-3">
                      <summary className="cursor-pointer text-ink-2">Limitations and validation</summary>
                      <ul className="mt-1.5 list-disc space-y-1 pl-5">{m.limitations.map((l: string) => <li key={l}>{l}</li>)}</ul>
                      <p className="mt-2">{m.validation?.summary}</p>
                    </details>
                  </div>
                  <button role="switch" aria-checked={m.enabled} aria-label={`${m.enabled ? 'Disable' : 'Enable'} ${m.title}`} onClick={() => toggle(m.id, !m.enabled)}
                    className="seg !p-0.5" style={{ minWidth: 64 }}>
                    <span className="block w-full rounded px-2 py-1 text-center text-[12px] font-semibold" style={m.enabled ? { background: 'var(--color-accent)', color: 'var(--color-accent-ink)' } : undefined}>{m.enabled ? 'On' : 'Off'}</span>
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      </Section>

      <Section title="Plugins" subtitle="Optional models install independently of the core app. A plugin that fails to load never affects the others." right={<Puzzle size={18} className="text-ink-3" aria-hidden />}>
        {data.plugins.length === 0 ? (
          <p className="text-[13px] text-ink-3">No plugins installed.</p>
        ) : (
          <ul className="space-y-1.5 text-[13px]">{data.plugins.map((p: any) => (
            <li key={p.source} className="flex items-start gap-2">{p.loaded ? <CheckCircle2 size={14} className="mt-0.5 text-good" /> : <CircleDashed size={14} className="mt-0.5 text-crit" />}
              <span><b>{p.source}</b>{p.loaded ? ` — adds ${p.modules.join(', ') || 'nothing'}` : ` — failed to load: ${p.error}`}</span></li>
          ))}</ul>
        )}
        <div className="mt-4 space-y-1.5 text-[12.5px] text-ink-3">
          <p>Add a module two ways: install a package that exposes the <code className="mono">{data.entry_point_group}</code> entry point, or drop a <code className="mono">.py</code> file into <code className="mono">{data.plugin_dir}</code>.</p>
          <p>A plugin may declare model weights; <code className="mono">uv run python ../scripts/setup_models.py &lt;key&gt;</code> then downloads them without touching anything else. Drop-in files are your own code and run with this app's permissions.</p>
        </div>
      </Section>
    </div>
  )
}

// ------------------------------------------------------------------------------------------------ detectors, keys
function Detectors() {
  const { data } = useApi<any>('/api/settings')
  if (!data) return <Spinner />
  return (
    <div className="space-y-5">
      <p className="text-ink-2">The tools available here decide what can be checked. Missing tools show up in cases as “not checked”. They are never treated as a negative result.</p>
      <Section title="Analyzers">
        <div className="space-y-1.5">{data.analyzers.map((a: any) => (
          <div key={a.name} className="flex items-start gap-3 border-b border-line/60 py-2 text-[13px]">
            {a.available ? <CheckCircle2 size={16} className="text-good" /> : <CircleDashed size={16} className="text-ink-3" />}
            <div className="flex-1"><div className="font-medium">{a.title}</div><div className="text-[11px] text-ink-3">{a.modalities.join(', ')}</div></div>
            {!a.available && <div className="max-w-md text-right text-[12px] text-ink-3">{a.reason}<div className="mono text-[11px]">{a.how_to_enable}</div></div>}
          </div>
        ))}</div>
      </Section>
      <Section title="Model weights" subtitle="Downloaded only by scripts/setup_models.py, with your approval. Each file is pinned by SHA-256 in models.lock.json.">
        <div className="space-y-2">{data.models.map((m: any) => (
          <div key={m.key} className="rounded-lg border border-line p-3 text-[13px]">
            <div className="flex items-center gap-2">{m.installed ? <CheckCircle2 size={15} className="text-good" /> : <CircleDashed size={15} className="text-ink-3" />}<b>{m.title}</b><span className="ml-auto text-ink-3">{m.size}</span></div>
            <div className="mt-1 text-[12px] text-ink-3">Trained on: {m.trained_on}. Blind spots: {m.blind_spots} Licence: {m.licence}.</div>
            {!m.installed && <div className="mono mt-1 text-[11px] text-ink-3">cd backend &amp;&amp; uv run python ../scripts/setup_models.py {m.key}</div>}
          </div>
        ))}</div>
      </Section>
    </div>
  )
}

function Keys() {
  const { data, reload } = useApi<any>('/api/settings')
  const [vals, setVals] = useState<Record<string, string>>({})
  if (!data) return <Spinner />
  return (
    <Section title="API keys" subtitle="Stored locally in data/settings.json. Adapters that send media to a third party also ask for your consent before each upload.">
      <div className="space-y-3">{Object.entries(data.keys).map(([k, v]: any) => (
        <div key={k} className="flex items-center gap-3 text-[13px]">
          <KeyRound size={14} className={v.configured ? 'text-good' : 'text-ink-3'} aria-hidden />
          <div className="w-72"><div className="mono text-[12px]">{k}</div><div className="text-[11px] text-ink-3">{v.description}</div></div>
          <input className="input flex-1" type="password" aria-label={k} placeholder={v.configured ? '•••••••• (configured)' : 'not configured'} value={vals[k] ?? ''} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} />
          <button className="btn" onClick={async () => { await api.put('/api/settings/keys', { name: k, value: vals[k] || null }); setVals({ ...vals, [k]: '' }); reload() }}>{vals[k] ? 'Save' : v.configured ? 'Remove' : 'Save'}</button>
        </div>
      ))}</div>
    </Section>
  )
}
