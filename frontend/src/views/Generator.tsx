/* eslint-disable @typescript-eslint/no-explicit-any */
import { Cpu, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { Empty, PageHeader, Pill, Section, SensitiveImg, Spinner } from '../components/ui'
import { useCase } from '../context'
import { useApi } from '../hooks'

const TIER: Record<string, { label: string; tone: 'good' | 'warn' | 'neutral' }> = {
  established: { label: 'Established', tone: 'good' }, supported: { label: 'Supported', tone: 'warn' }, suggested: { label: 'Suggested only', tone: 'neutral' },
}
const KIND_TONE: Record<string, 'good' | 'warn' | 'neutral'> = { signed: 'good', declared: 'warn', watermark: 'warn', classifier: 'neutral', inherited: 'neutral' }

export default function Generator() {
  const { caseId, tick, data: cd } = useCase()
  const { data, error } = useApi<any>(`/api/cases/${caseId}/generator`, [tick])
  const [sel, setSel] = useState<string | null>(null)
  if (error && !data) return <div className="card max-w-2xl border-crit/60 p-5 text-sm">Could not load generator attribution: {error}</div>
  if (!data) return <Spinner label="Reading the evidence" />
  const list: any[] = data.media
  const m = list.find((x) => x.media_id === sel) ?? list[0]
  const thumb = cd.media.find((x) => x.id === m?.media_id)?.thumb

  return (
    <div className="max-w-5xl">
      <PageHeader step="Investigate deeper" title="AI generator attribution"
        question="Which family of AI tool probably made this — and how much does each piece of evidence really prove? We only name a family when the evidence supports it." />
      {list.length === 0 ? <Empty>No analysed media yet.</Empty> : <>
        {list.length > 1 && (
          <div className="mb-4 flex flex-wrap gap-2">{list.map((x) => (
            <button key={x.media_id} className={`btn px-3 py-1.5 text-[12px] ${x.media_id === m.media_id ? 'border-ink' : ''}`} onClick={() => setSel(x.media_id)}>
              {x.role === 'primary' ? 'Suspect media' : x.label.slice(0, 28)}{x.possible_family ? ` · ${x.possible_family.split(' (')[0]}` : ''}</button>))}</div>
        )}
        <div className="card mb-4 overflow-hidden">
          <div className="flex gap-5 p-5">
            <SensitiveImg caseId={caseId} sha={thumb} blur={cd.blur} className="h-24 w-24 shrink-0 rounded bg-black" />
            <div className="min-w-0 flex-1">
              <div className="label flex items-center gap-1.5"><Cpu size={12} /> Result</div>
              <div className="mt-1 text-[22px] font-semibold leading-snug">{m.headline}</div>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {m.tier && <Pill tone={TIER[m.tier].tone}>{TIER[m.tier].label}</Pill>}
                {m.conflict && <Pill tone="crit">Conflicting evidence</Pill>}
                {!m.possible_family && !m.conflict && <Pill>No family named</Pill>}
              </div>
            </div>
          </div>
          <div className="space-y-1.5 border-t border-line bg-bg/40 px-5 py-3 text-[12.5px] text-ink-2">
            {m.cautions.map((c: string, i: number) => <div key={i} className="flex gap-2"><TriangleAlert size={13} className={`mt-0.5 shrink-0 ${i === 0 ? 'text-warn' : 'text-ink-3'}`} />{c}</div>)}
          </div>
        </div>

        <Section title="Evidence ladder" subtitle="Each row says what was found, what kind of evidence it is, and how far it can be trusted. Kinds are never added up just because there are many of them.">
          {m.candidates.length === 0 ? <div className="text-[13px] text-ink-3">Nothing in the file or from the detectors points to a particular generator.</div> : (
            <div className="space-y-4">{m.candidates.map((c: any) => (
              <div key={c.family} className="rounded-lg border border-line">
                <div className="flex items-center justify-between border-b border-line px-4 py-2.5"><b>{c.family}</b><Pill tone={TIER[c.tier].tone}>{TIER[c.tier].label}</Pill></div>
                <ul className="divide-y divide-line/60">{c.evidence.map((e: any, i: number) => (
                  <li key={i} className="grid gap-2 px-4 py-2.5 text-[12.5px] md:grid-cols-[180px_1fr]">
                    <div><Pill tone={KIND_TONE[e.kind]}>{e.label}</Pill></div>
                    <div><div className="text-ink-2">{e.text}</div><div className="mt-0.5 text-[11.5px] text-ink-3">How far to trust it: {e.reliability}</div></div>
                  </li>))}</ul>
              </div>))}</div>
          )}
        </Section>

        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <Section title="What was checked">
            <ul className="space-y-1.5 text-[12.5px]">
              {[['Signed content credentials (C2PA)', m.checked.c2pa], ['Generation parameters in the file', m.checked.embedded], ['Software / creator-tool fields', m.checked.software],
                ['Open invisible watermark', m.checked.watermark], ['Source classifier (Hive)', m.checked.classifier]].map(([k, v]) => (
                <li key={k as string} className="flex justify-between gap-3"><span className="text-ink-2">{k}</span><span className={/found|valid|decoded|ran|names/.test(String(v)) && !/not found|none/.test(String(v)) ? 'text-ink' : 'text-ink-3'}>{String(v)}</span></li>))}
            </ul>
            <div className="label mb-1.5 mt-4">Not checked (and why)</div>
            <ul className="space-y-1.5 text-[12px] text-ink-3">{m.not_checked.map((n: any) => <li key={n.name}><span className="text-ink-2">{n.name}</span> — {n.why}</li>)}</ul>
          </Section>
          <Section title="What to do next">
            <ul className="list-disc space-y-1.5 pl-5 text-[13px] text-ink-2">{m.next_steps.map((n: string) => <li key={n}>{n}</li>)}</ul>
            <div className="label mb-1.5 mt-4">How to read the evidence kinds</div>
            <ul className="space-y-1.5 text-[11.5px] text-ink-3">{Object.entries(data.media[0].reliability_scale).map(([k, v]: any) => <li key={k}><b className="text-ink-2">{v.label}:</b> {v.meaning}</li>)}</ul>
          </Section>
        </div>
      </>}
    </div>
  )
}
