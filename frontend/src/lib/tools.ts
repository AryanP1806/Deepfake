import { AudioLines, Cpu, Network, PenLine, ScanSearch, ShieldCheck, Smartphone, Video, type LucideIcon } from 'lucide-react'

/** Every analysis tool opens the part of a case that does the work. A tool needs a case (it analyses that case's media). */
export interface Tool {
  key: string
  title: string
  short: string          // card subtitle
  about: string          // what the tool does, shown on its picker page
  route: string          // sub-path inside a case
  icon: LucideIcon
  hue: string            // decorative icon tint (token); the title carries the meaning
  modality?: 'image' | 'video' | 'audio'
  moduleId?: string      // forensic module that backs it (for availability)
}

export const TOOLS: Record<string, Tool> = {
  ai: { key: 'ai', title: 'AI Detection', short: 'Real or AI-generated?', about: 'Is the picture or video AI-generated, face-swapped or edited? One plain verdict with the evidence beneath it.', route: 'decomposition', icon: ScanSearch, hue: 'var(--hue-1)' },
  device: { key: 'device', title: 'Device Fingerprint', short: 'Which device / camera?', about: 'What the file says about its camera, and a sensor-noise (PRNU) comparison against reference photos from a suspected device. A match supports, never proves.', route: 'forensics', icon: Smartphone, hue: 'var(--hue-2)', modality: 'image', moduleId: 'prnu' },
  editing: { key: 'editing', title: 'Editing Analysis', short: 'Manipulation traces', about: 'Error levels, noise, resampling, double compression and copied regions, with a map of where independent cues agree.', route: 'forensics', icon: PenLine, hue: 'var(--hue-3)', modality: 'image', moduleId: 'editing' },
  video: { key: 'video', title: 'Video Forensics', short: 'Capture & processing', about: 'Frame timing, codec and container traces, camera motion and rolling shutter.', route: 'forensics', icon: Video, hue: 'var(--hue-4)', modality: 'video', moduleId: 'video_acquisition' },
  audio: { key: 'audio', title: 'Audio Forensics', short: 'Voice & environment', about: 'Mains hum, reverberation, noise floor, clipping and audio/video synchronisation.', route: 'forensics', icon: AudioLines, hue: 'var(--hue-5)', modality: 'audio', moduleId: 'audio_forensics' },
  provenance: { key: 'provenance', title: 'Provenance', short: 'Metadata & C2PA', about: 'What the file declares about its origin: EXIF, C2PA content credentials, AI watermarks and generator metadata.', route: 'forensics', icon: ShieldCheck, hue: 'var(--hue-6)', moduleId: 'provenance' },
  image: { key: 'image', title: 'Image Forensics', short: 'Pixel-level checks', about: 'Error-level analysis, noise, spectrum, faces and every detector, for the photos in a case.', route: 'decomposition', icon: ScanSearch, hue: 'var(--hue-1)', modality: 'image' },
  generator: { key: 'generator', title: 'AI Generator Check', short: 'Which tool family?', about: 'Which generator family the evidence points to, how reliable each signal is, and what was not checked.', route: 'generator', icon: Cpu, hue: 'var(--hue-2)' },
  graph: { key: 'graph', title: 'Graph & Link Analysis', short: 'Accounts and leads', about: 'Accounts, versions and ranked leads, with observed links separated from hypotheses.', route: 'leads', icon: Network, hue: 'var(--hue-6)' },
}

export const MODULE_CARDS = ['ai', 'device', 'editing', 'video', 'audio', 'provenance']
