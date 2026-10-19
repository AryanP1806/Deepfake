import { api, type Case } from '../api'

export interface InvestigationInput {
  links: string[]
  file: File | null
  incident: string
  title: string
  description: string
  intimate: boolean
  minors: boolean
  findCopies: boolean
}

const URL_RE = /\bhttps?:\/\/[^\s<>"')\]]+|\b(?:www\.)[^\s<>"')\]]+/gi

/** Links found in pasted text (a caption, a message, an email), trimmed of trailing punctuation, de-duplicated. */
export function extractUrls(text: string): string[] {
  const found = (text.match(URL_RE) ?? []).map((u) => u.replace(/[.,;:!?]+$/, ''))
  return [...new Set(found)]
}

export class InvestigationError extends Error {}

/** Create the case and start the investigation (the same calls the old form made). Returns the new case id. */
export async function startInvestigation(i: InvestigationInput): Promise<string> {
  const urls = i.links.map((u) => u.trim()).filter(Boolean)
  if (!urls.length && !i.file) throw new InvestigationError('Paste a link to the photo, video or post, or choose a file.')
  const sensitive = i.intimate || i.minors
  const base = {
    title: i.title, incident_type: i.incident, description: i.description,
    safety: { intimate: i.intimate, minors_possible: i.minors }, find_copies: i.findCopies && !sensitive,
  }
  if (i.file) {  // the file is the suspect media; any links are places it was seen
    const c = await api.post<Case>('/api/cases', { ...base, title: i.title || i.file.name })
    const fd = new FormData()
    fd.append('file', i.file)
    fd.append('role', 'primary')
    await api.post(`/api/cases/${c.id}/media`, fd)
    if (urls.length) await api.post(`/api/cases/${c.id}/investigate`, { urls, find_copies: base.find_copies, depth: 1 })
    return c.id
  }
  const c = await api.post<Case & { job?: unknown }>('/api/cases', { ...base, urls })
  if (!c.job) {  // an up-to-date backend always starts the investigation when links are given
    throw new InvestigationError('The case was created, but the backend did not start investigating the link. It is probably an old version: restart it (see the red banner), then press Investigate again.')
  }
  return c.id
}
