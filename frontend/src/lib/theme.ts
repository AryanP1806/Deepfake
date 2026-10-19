/** Theme model: three themes x light/dark, a changeable accent colour, reduced-motion control.
 *
 * The accent is DERIVED so it is always usable: text on an accent-coloured button and accent-coloured text/links on the
 * theme's surfaces both reach WCAG AA (4.5:1). If the colour the user picked cannot do that, it is nudged in lightness and
 * the Settings page says so. Nothing here uses gradients. */

export type ThemeId = 'workbench' | 'neomorphism' | 'claymorphism'
export type ModeChoice = 'light' | 'dark' | 'system'
export type MotionChoice = 'system' | 'reduce' | 'full'
export interface Profile { name: string; role: string }
export interface Prefs { theme: ThemeId; mode: ModeChoice; accent: string; motion: MotionChoice; profile?: Profile; updated_at?: string | null }

export const DEFAULT_PREFS: Prefs = { theme: 'neomorphism', mode: 'light', accent: '#2f6fe0', motion: 'system', profile: { name: '', role: 'Investigator' }, updated_at: null }

export const THEMES: { id: ThemeId; name: string; blurb: string }[] = [
  { id: 'workbench', name: 'Workbench', blurb: 'Flat and ruled, like a lab notebook. Densest; best for long sessions.' },
  { id: 'neomorphism', name: 'Neomorphism', blurb: 'One soft material; controls rise from the surface and press into it.' },
  { id: 'claymorphism', name: 'Claymorphism', blurb: 'Rounded, tactile and warm, with a gentle puffed look.' },
]

export const ACCENTS: { name: string; hex: string }[] = [
  { name: 'Blue', hex: '#2f6fe0' }, { name: 'Slate blue', hex: '#2f6f9f' }, { name: 'Teal', hex: '#1f7a72' }, { name: 'Moss', hex: '#5b7a2f' },
  { name: 'Ochre', hex: '#a8741a' }, { name: 'Brick', hex: '#b24a3a' }, { name: 'Plum', hex: '#7a4a7a' }, { name: 'Graphite', hex: '#4a4f57' },
]

/** Backgrounds the accent text must stay legible on, per theme and mode (surface and page background). */
export const SURFACES: Record<`${ThemeId}:${'light' | 'dark'}`, [string, string]> = {
  'workbench:light': ['#fbfbf9', '#f0efea'], 'workbench:dark': ['#1a1a19', '#121211'],
  'neomorphism:light': ['#e3e7ee', '#e3e7ee'], 'neomorphism:dark': ['#2a2e35', '#2a2e35'],
  'claymorphism:light': ['#f7f2ea', '#ece5db'], 'claymorphism:dark': ['#322c27', '#27221e'],
}

// ---------------------------------------------------------------------------------------------------- colour maths
export const HEX = /^#[0-9a-fA-F]{6}$/
type RGB = [number, number, number]
export const hexToRgb = (h: string): RGB => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)]
export const rgbToHex = ([r, g, b]: RGB) => '#' + [r, g, b].map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join('')

export function luminance(hex: string): number {
  const f = (c: number) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4 }
  const [r, g, b] = hexToRgb(hex)
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}
export function contrast(a: string, b: string): number {
  const la = luminance(a), lb = luminance(b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}
function toHsl(hex: string): [number, number, number] {
  const [r, g, b] = hexToRgb(hex).map((v) => v / 255)
  const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2
  if (max === min) return [0, 0, l]
  const d = max - min
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
  const h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4
  return [h * 60, s, l]
}
function fromHsl(h: number, s: number, l: number): string {
  const c = (1 - Math.abs(2 * l - 1)) * s, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), m = l - c / 2
  const [r, g, b] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x]
  return rgbToHex([(r + m) * 255, (g + m) * 255, (b + m) * 255])
}
export const mix = (a: string, b: string, t: number) => rgbToHex(hexToRgb(a).map((v, i) => v * t + hexToRgb(b)[i] * (1 - t)) as RGB)

const INK_LIGHT = '#ffffff'
const INK_DARK = '#14130f'

export interface AccentSet { fill: string; ink: string; text: string; soft: string; fillContrast: number; textContrast: number; adjusted: boolean }

export function deriveAccent(hex: string, theme: ThemeId, mode: 'light' | 'dark'): AccentSet {
  const base = HEX.test(hex) ? hex : DEFAULT_PREFS.accent
  const [surface, bg] = SURFACES[`${theme}:${mode}`]
  let fill = base, adjusted = false
  // 1. a fill that carries readable button text
  const best = (f: string) => (contrast(f, INK_LIGHT) >= contrast(f, INK_DARK) ? INK_LIGHT : INK_DARK)
  let ink = best(fill)
  for (let i = 0; i < 40 && contrast(fill, ink) < 4.5; i++) {
    const [h, s, l] = toHsl(fill)
    fill = fromHsl(h, s, ink === INK_LIGHT ? Math.max(0, l - 0.02) : Math.min(1, l + 0.02))
    adjusted = true
    ink = best(fill)
  }
  // 2. accent used as text/links/icons on the theme's surfaces
  let text = fill
  const worst = (t: string) => Math.min(contrast(t, surface), contrast(t, bg))
  for (let i = 0; i < 50 && worst(text) < 4.5; i++) {
    const [h, s, l] = toHsl(text)
    text = fromHsl(h, s, mode === 'light' ? Math.max(0, l - 0.02) : Math.min(1, l + 0.02))
  }
  return { fill, ink, text, soft: mix(fill, surface, mode === 'light' ? 0.13 : 0.2), fillContrast: contrast(fill, ink), textContrast: worst(text), adjusted: adjusted || text !== base && false }
}

export function accentVars(a: AccentSet): Record<string, string> {
  return { '--color-accent': a.fill, '--color-accent-ink': a.ink, '--color-accent-text': a.text, '--color-accent-soft': a.soft }
}

// ---------------------------------------------------------------------------------------------------- applying
export const resolveMode = (m: ModeChoice): 'light' | 'dark' =>
  m === 'system' ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : m

const LS_PREFS = 'dfir.prefs'
const LS_CACHE = 'dfir.themecache'

export function loadLocal(): Prefs {
  try {
    const p = JSON.parse(localStorage.getItem(LS_PREFS) || '{}')
    return sanitize(p)
  } catch { return { ...DEFAULT_PREFS } }
}

export function sanitize(p: Partial<Prefs>): Prefs {
  return {
    theme: THEMES.some((t) => t.id === p.theme) ? (p.theme as ThemeId) : DEFAULT_PREFS.theme,
    mode: ['light', 'dark', 'system'].includes(p.mode as string) ? (p.mode as ModeChoice) : DEFAULT_PREFS.mode,
    accent: HEX.test(p.accent ?? '') ? (p.accent as string) : DEFAULT_PREFS.accent,
    motion: ['system', 'reduce', 'full'].includes(p.motion as string) ? (p.motion as MotionChoice) : DEFAULT_PREFS.motion,
    profile: { name: String(p.profile?.name ?? '').slice(0, 60), role: String(p.profile?.role || 'Investigator').slice(0, 40) },
    updated_at: p.updated_at ?? null,
  }
}

/** Write attributes + derived accent variables to <html>, cache them for the next pre-paint, and tell charts to redraw. */
export function applyPrefs(p: Prefs): { mode: 'light' | 'dark'; accent: AccentSet } {
  const root = document.documentElement
  const mode = resolveMode(p.mode)
  const accent = deriveAccent(p.accent, p.theme, mode)
  root.setAttribute('data-theme', p.theme)
  root.setAttribute('data-mode', mode)
  root.setAttribute('data-motion', p.motion)
  const vars = accentVars(accent)
  for (const [k, v] of Object.entries(vars)) root.style.setProperty(k, v)
  try {
    localStorage.setItem(LS_PREFS, JSON.stringify(p))
    const cache = JSON.parse(localStorage.getItem(LS_CACHE) || '{}')
    cache[`${p.theme}:${mode}:${p.accent}`] = vars
    const keys = Object.keys(cache)
    if (keys.length > 24) delete cache[keys[0]]
    localStorage.setItem(LS_CACHE, JSON.stringify(cache))
  } catch { /* private mode: the server copy still persists */ }
  document.querySelector('meta[name="color-scheme"]')?.setAttribute('content', mode)
  window.dispatchEvent(new CustomEvent('dfir-theme'))
  return { mode, accent }
}

/** Read a design token as a concrete colour string (for canvas/SVG libraries that cannot use CSS variables). */
export const token = (name: string, fallback = '#888888') =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
