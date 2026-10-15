/* eslint-disable @typescript-eslint/no-explicit-any */
import { CheckCircle2, CircleDashed, KeyRound } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api'
import { TopNav } from './Lab'
import { Section, Spinner } from '../components/ui'
import { useApi } from '../hooks'

export default function Settings() {
  const { data, reload } = useApi<any>('/api/settings')
  const [vals, setVals] = useState<Record<string, string>>({})
  if (!data) return <div className="p-10"><Spinner /></div>
  return (
    <div className="mx-auto max-w-5xl px-8 py-8">
      <TopNav active="settings" />
      <h1 className="mt-8 text-2xl font-semibold">Detectors, models & external services</h1>
      <p className="mt-1 text-ink-3">The tools available here decide what can be checked. Missing tools show up in cases as "not checked". They are never treated as a negative result.</p>
      <Section className="mt-6" title="Analyzers">
        <div className="space-y-1.5">{data.analyzers.map((a: any) => (
          <div key={a.name} className="flex items-start gap-3 border-b border-line/60 py-2 text-[13px]">
            {a.available ? <CheckCircle2 size={16} className="text-good" /> : <CircleDashed size={16} className="text-ink-3" />}
            <div className="flex-1"><div className="font-medium">{a.title}</div><div className="text-[11px] text-ink-3">{a.modalities.join(', ')}</div></div>
            {!a.available && <div className="max-w-md text-right text-[12px] text-ink-3">{a.reason}<div className="mono text-[11px]">{a.how_to_enable}</div></div>}
          </div>
        ))}</div>
      </Section>
      <Section className="mt-5" title="Model weights" subtitle="Downloaded only by scripts/setup_models.py, with your approval. Each file is pinned by SHA-256 in models.lock.json.">
        <div className="space-y-2">{data.models.map((m: any) => (
          <div key={m.key} className="rounded-lg border border-line p-3 text-[13px]">
            <div className="flex items-center gap-2">{m.installed ? <CheckCircle2 size={15} className="text-good" /> : <CircleDashed size={15} className="text-ink-3" />}<b>{m.title}</b><span className="ml-auto text-ink-3">{m.size}</span></div>
            <div className="mt-1 text-[12px] text-ink-3">Trained on: {m.trained_on}. Blind spots: {m.blind_spots} Licence: {m.licence}.</div>
            {!m.installed && <div className="mono mt-1 text-[11px] text-ink-3">cd backend && uv run python ../scripts/setup_models.py {m.key}</div>}
          </div>
        ))}</div>
      </Section>
      <Section className="mt-5" title="API keys" subtitle="Stored locally in data/settings.json. Adapters that send media to a third party also ask for your consent before each upload.">
        <div className="space-y-2">{Object.entries(data.keys).map(([k, v]: any) => (
          <div key={k} className="flex items-center gap-3 text-[13px]">
            <KeyRound size={14} className={v.configured ? 'text-good' : 'text-ink-3'} />
            <div className="w-72"><div className="mono text-[12px]">{k}</div><div className="text-[11px] text-ink-3">{v.description}</div></div>
            <input className="input flex-1" type="password" placeholder={v.configured ? '•••••••• (configured)' : 'not configured'} value={vals[k] ?? ''} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} />
            <button className="btn" onClick={async () => { await api.put('/api/settings/keys', { name: k, value: vals[k] || null }); setVals({ ...vals, [k]: '' }); reload() }}>{vals[k] ? 'Save' : v.configured ? 'Remove' : 'Save'}</button>
          </div>
        ))}</div>
      </Section>
    </div>
  )
}
