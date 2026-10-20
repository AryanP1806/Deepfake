import { ArrowRight, ChevronDown, ClipboardPaste, Image as ImageIcon, Link2, Play, SlidersHorizontal, Upload } from 'lucide-react'
import { useId, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { INCIDENT_CHOICES } from '../../api'
import { DemoBlocked } from '../../lib/demo'
import { extractUrls, InvestigationError, startInvestigation } from '../../lib/investigate'

type Tab = 'url' | 'file' | 'text'
const TABS: { id: Tab; label: string; icon: typeof Link2 }[] = [
  { id: 'url', label: 'Paste URL', icon: Link2 },
  { id: 'file', label: 'Upload File', icon: Upload },
  { id: 'text', label: 'Paste / Type Text', icon: ClipboardPaste },
]

/** Decorative stack of tiles (no data, hidden from assistive tech). */
function Illustration() {
  return (
    <div className="pointer-events-none relative hidden h-44 w-60 shrink-0 select-none xl:block" aria-hidden>
      <div className="tile absolute right-24 top-4 grid h-24 w-24 -rotate-6 place-items-center text-accent-text"><ImageIcon size={34} /></div>
      <div className="tile absolute right-2 top-0 grid h-24 w-24 rotate-3 place-items-center text-accent-text"><Play size={34} /></div>
      <div className="tile absolute right-14 top-20 grid h-20 w-20 -rotate-2 place-items-center text-accent-text"><Link2 size={30} /></div>
    </div>
  )
}

export function NewInvestigation({ expanded = false, copiesFocus = false }: { expanded?: boolean; copiesFocus?: boolean }) {
  const nav = useNavigate()
  const uid = useId()
  const [tab, setTab] = useState<Tab>('url')
  const [link, setLink] = useState('')
  const [text, setText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [drag, setDrag] = useState(false)
  const [adv, setAdv] = useState(expanded)
  const [incident, setIncident] = useState('other')
  const [moreLinks, setMoreLinks] = useState('')
  const [title, setTitle] = useState('')
  const [notes, setNotes] = useState('')
  const [intimate, setIntimate] = useState(false)
  const [minors, setMinors] = useState(false)
  const [findCopies, setFindCopies] = useState(true)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const fileInput = useRef<HTMLInputElement>(null)
  const sensitive = intimate || minors

  const go = async () => {
    setErr('')
    let links: string[] = []
    let description = notes
    if (tab === 'url') links = [link, ...moreLinks.split(/\s+/)]
    else if (tab === 'text') {
      links = [...extractUrls(text), ...moreLinks.split(/\s+/)]
      if (!links.filter(Boolean).length && !file) { setErr('No link was found in that text. Paste text that contains a link to the photo, video or post, or use Upload File.'); return }
      description = [notes, `Pasted text:\n${text.trim()}`].filter(Boolean).join('\n\n')
    } else links = moreLinks.split(/\s+/)
    setBusy(true)
    try {
      const id = await startInvestigation({ links, file: tab === 'file' ? file : null, incident, title, description, intimate, minors, findCopies: findCopies || copiesFocus })
      nav(`/case/${id}/overview`)
    } catch (e) {
      if (!(e instanceof DemoBlocked)) setErr(e instanceof InvestigationError ? e.message : (e as Error).message)
    } finally { setBusy(false) }
  }

  const onKeyTabs = (e: React.KeyboardEvent) => {
    const i = TABS.findIndex((t) => t.id === tab)
    if (e.key === 'ArrowRight') { e.preventDefault(); setTab(TABS[(i + 1) % TABS.length].id) }
    if (e.key === 'ArrowLeft') { e.preventDefault(); setTab(TABS[(i + TABS.length - 1) % TABS.length].id) }
  }
  const pickFile = (f?: File | null) => { if (f) { setFile(f); setErr('') } }

  return (
    <section className="card p-6 lg:p-7" aria-labelledby={`${uid}-h`}>
      <div className="flex items-start justify-between gap-6">
        <div className="min-w-0">
          <span className="badge" style={{ '--b': 'var(--color-accent-text)' } as React.CSSProperties}><span className="dot" style={{ '--d': 'var(--color-accent-text)' } as React.CSSProperties} />{copiesFocus ? 'RELATED COPIES' : 'NEW INVESTIGATION'}</span>
          <h1 id={`${uid}-h`} className="display mt-3.5 text-[34px] font-extrabold leading-[1.1] text-ink md:text-[40px]">
            {copiesFocus ? <>Find <span className="text-accent-text">other copies</span> of a fake</> : <>Analyse any <span className="text-accent-text">photo, video</span> or link</>}
          </h1>
          <p className="mt-3 max-w-xl text-[15px] leading-relaxed text-ink-2">
            {copiesFocus
              ? 'Paste where you saw it. We save the page as evidence, then search for the same picture or video on other sites, accounts and archives, and show how the copies relate.'
              : 'Start an investigation from a file or a URL. We capture, analyse, trace, and preserve evidence, and show you how it may have been created and spread.'}
          </p>
        </div>
        <Illustration />
      </div>

      <div role="tablist" aria-label="How to start" onKeyDown={onKeyTabs} className="mt-6 flex flex-wrap gap-x-1 border-b border-line">
        {TABS.map((t) => (
          <button key={t.id} role="tab" id={`${uid}-t-${t.id}`} aria-selected={tab === t.id} aria-controls={`${uid}-p`} tabIndex={tab === t.id ? 0 : -1} onClick={() => setTab(t.id)}
            className={`-mb-px flex items-center gap-2 border-b-2 px-4 py-2.5 text-[14px] font-semibold ${tab === t.id ? 'border-accent-text text-accent-text' : 'border-transparent text-ink-2 hover:text-ink'}`}>
            <t.icon size={16} aria-hidden />{t.label}
          </button>
        ))}
      </div>

      <div id={`${uid}-p`} role="tabpanel" aria-labelledby={`${uid}-t-${tab}`} className="pt-5">
        <form onSubmit={(e) => { e.preventDefault(); go() }} className="flex flex-col gap-3 sm:flex-row sm:items-stretch">
          {tab === 'url' && (
            <div className="well flex min-w-0 flex-1 items-center gap-2.5 px-4 py-1">
              <Link2 size={17} className="shrink-0 text-ink-3" aria-hidden />
              <input value={link} onChange={(e) => setLink(e.target.value)} aria-label="Link to the photo, video or post" inputMode="url" autoComplete="off" spellCheck={false}
                placeholder="https://… (Instagram, YouTube, Facebook, X, website, etc.)" className="min-h-[44px] min-w-0 flex-1 bg-transparent text-[14px] text-ink outline-none placeholder:text-ink-3 focus-visible:!outline-none" />
            </div>
          )}
          {tab === 'file' && (
            <div className={`well flex min-w-0 flex-1 items-center gap-3 border-dashed px-4 py-2.5 ${drag ? '!border-accent-text' : ''}`} style={{ borderStyle: 'dashed' }}
              onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)} onDrop={(e) => { e.preventDefault(); setDrag(false); pickFile(e.dataTransfer.files?.[0]) }}>
              <Upload size={18} className="shrink-0 text-ink-3" aria-hidden />
              <div className="min-w-0 flex-1 text-[14px]">{file ? <span className="font-medium text-ink">{file.name}</span> : <span className="text-ink-3">Drop a photo, video or audio file here, or</span>}</div>
              <input ref={fileInput} type="file" hidden accept="image/*,video/*,audio/*" onChange={(e) => pickFile(e.target.files?.[0])} aria-label="Choose a file" />
              <button type="button" className="btn !min-h-[36px]" onClick={() => fileInput.current?.click()}>{file ? 'Change file' : 'Choose file'}</button>
            </div>
          )}
          {tab === 'text' && (
            <div className="well min-w-0 flex-1 px-4 py-2">
              <textarea value={text} onChange={(e) => setText(e.target.value)} aria-label="Pasted text containing a link" rows={3} placeholder="Paste a message, caption or email. Links in it are found and investigated."
                className="w-full resize-y bg-transparent text-[14px] text-ink outline-none placeholder:text-ink-3 focus-visible:!outline-none" />
            </div>
          )}
          <button type="submit" disabled={busy} className="btn btn-primary !min-h-[48px] justify-center whitespace-nowrap !px-6 text-[14px] sm:self-stretch">
            {busy ? 'Starting…' : <>Start Investigation <ArrowRight size={16} aria-hidden /></>}
          </button>
        </form>
        {err && <p role="alert" className="mt-3 text-[13px] font-medium text-crit">{err}</p>}

        <div className="mt-3 flex flex-wrap items-center justify-between gap-x-6 gap-y-2 text-[12.5px] text-ink-2">
          <p className="min-w-0">Supported: public links from major platforms. We capture the page, extract media, and analyse it. A login-protected post can’t be read; upload a saved copy instead.</p>
          <button type="button" className="inline-flex shrink-0 items-center gap-1.5 font-semibold text-ink-2 hover:text-ink" aria-expanded={adv} aria-controls={`${uid}-adv`} onClick={() => setAdv(!adv)}>
            <SlidersHorizontal size={14} aria-hidden /> Advanced options <ChevronDown size={14} className={adv ? 'rotate-180' : ''} aria-hidden />
          </button>
        </div>

        {adv && (
          <div id={`${uid}-adv`} className="mt-5 space-y-5 border-t border-line pt-5">
            <fieldset>
              <legend className="label mb-2">What kind of problem is this?</legend>
              <div role="radiogroup" className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {INCIDENT_CHOICES.map((o) => (
                  <button key={o.key} type="button" role="radio" aria-checked={incident === o.key} onClick={() => { setIncident(o.key); if (o.key === 'ncii' || o.key === 'sextortion') setIntimate(true) }}
                    className={`tile p-3 text-left ${incident === o.key ? '!border-accent-text !bg-accent-soft' : ''}`} style={incident === o.key ? { borderColor: 'var(--color-accent-text)' } : undefined}>
                    <div className="text-[13px] font-semibold leading-snug text-ink">{o.title}</div>
                    {o.hint && <div className="mt-0.5 text-[11.5px] text-ink-3">{o.hint}</div>}
                  </button>
                ))}
              </div>
            </fieldset>
            <fieldset className="well space-y-2.5 p-4">
              <legend className="label px-1">Safety</legend>
              <label className="flex items-start gap-2.5 text-[13.5px]"><input type="checkbox" className="mt-1" checked={intimate} onChange={(e) => setIntimate(e.target.checked)} />
                <span>The media is intimate or sexual <span className="text-ink-3">· previews are blurred and nothing is sent to outside services without asking you</span></span></label>
              <label className="flex items-start gap-2.5 text-[13.5px]"><input type="checkbox" className="mt-1" checked={minors} onChange={(e) => setMinors(e.target.checked)} />
                <span>A child may be shown <span className="text-ink-3">· nothing is downloaded or sent anywhere; you are guided to report the web address only</span></span></label>
            </fieldset>
            <div className="grid gap-4 md:grid-cols-2">
              <div><label htmlFor={`${uid}-more`} className="label mb-1.5 block">{tab === 'url' ? 'Other places you saw it (one link per line)' : 'Places you saw it (one link per line)'}</label>
                <textarea id={`${uid}-more`} className="input mono min-h-20 text-[12px]" placeholder={'https://instagram.com/p/…\nhttps://t.me/channel/123'} value={moreLinks} onChange={(e) => setMoreLinks(e.target.value)} /></div>
              <div><label htmlFor={`${uid}-notes`} className="label mb-1.5 block">What happened, in your words (optional)</label>
                <textarea id={`${uid}-notes`} className="input min-h-20" value={notes} onChange={(e) => setNotes(e.target.value)} /></div>
              <div><label htmlFor={`${uid}-title`} className="label mb-1.5 block">Name for this case (optional)</label>
                <input id={`${uid}-title`} className="input" placeholder="Filled in automatically" value={title} onChange={(e) => setTitle(e.target.value)} /></div>
              <label className={`flex items-start gap-2.5 text-[13.5px] ${sensitive ? 'opacity-60' : ''}`}>
                <input type="checkbox" className="mt-1" disabled={sensitive} checked={(findCopies || copiesFocus) && !sensitive} onChange={(e) => setFindCopies(e.target.checked)} />
                <span>Also search the web for copies using the page’s caption and image
                  <span className="block text-[12px] text-ink-3">{sensitive ? 'Turned off for sensitive cases: nothing is sent to search services unless you choose to later.' : 'The caption goes to DuckDuckGo, and the image to any image-search service you configured. Links on the page are always followed.'}</span></span>
              </label>
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
