import { useState } from 'react'

/** Per-frame signals as small multiples on a shared time axis (never a dual axis).
 *  Each row has its own y-range; one crosshair reads every row at once. */
const META: Record<string, { label: string; fmt: (v: number) => string; domain?: [number, number] }> = {
  ufd: { label: 'Learned detector (uncalibrated)', fmt: (v) => v.toFixed(2), domain: [0, 1] },
  ela: { label: 'Mean error level', fmt: (v) => v.toFixed(2) },
  spectral: { label: 'Spectral peak score', fmt: (v) => v.toFixed(1) },
  faces: { label: 'Faces detected', fmt: (v) => String(v) },
}

export function SeriesChart({ series }: { series: Record<string, number[] | null> & { t: number[] } }) {
  const t = series.t
  const keys = Object.keys(META).filter((k) => Array.isArray(series[k]) && (series[k] as number[]).length === t.length)
  const [hover, setHover] = useState<number | null>(null)
  if (!t?.length || !keys.length) return null
  const W = 640, rowH = 46, padL = 150, padR = 54, gap = 10
  const H = keys.length * (rowH + gap) + 18
  const x = (i: number) => padL + ((t[i] - t[0]) / Math.max(1e-6, t[t.length - 1] - t[0])) * (W - padL - padR)
  return (
    <div>
      <div className="label mb-1">Per-frame signals</div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
          const px = ((e.clientX - r.left) / r.width) * W
          let best = 0
          t.forEach((_, i) => { if (Math.abs(x(i) - px) < Math.abs(x(best) - px)) best = i })
          setHover(best)
        }}>
        {keys.map((k, row) => {
          const vals = series[k] as number[]
          const [lo, hi] = META[k].domain ?? [Math.min(...vals), Math.max(...vals) || 1]
          const y0 = row * (rowH + gap)
          const y = (v: number) => y0 + rowH - ((v - lo) / Math.max(1e-6, hi - lo)) * (rowH - 6) - 3
          const d = vals.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('')
          return (
            <g key={k}>
              <text x={0} y={y0 + rowH / 2 + 4} fontSize={11} fill="var(--color-ink-2)">{META[k].label}</text>
              <line x1={padL} x2={W - padR} y1={y0 + rowH} y2={y0 + rowH} stroke="var(--color-line)" />
              <path d={d} fill="none" stroke="var(--color-ink-2)" strokeWidth={2} strokeLinejoin="round" />
              {hover !== null && <circle cx={x(hover)} cy={y(vals[hover])} r={4} fill="var(--color-ink)" stroke="var(--color-surface)" strokeWidth={2} />}
              <text x={W - padR + 6} y={y0 + rowH / 2 + 4} fontSize={11} fill="var(--color-ink)" className="mono">
                {META[k].fmt(hover !== null ? vals[hover] : vals[vals.length - 1])}
              </text>
            </g>
          )
        })}
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={0} y2={H - 18} stroke="var(--color-ink-3)" strokeDasharray="3 3" />}
        <text x={padL} y={H - 4} fontSize={10} fill="var(--color-ink-3)">{t[0].toFixed(1)} s</text>
        <text x={W - padR} y={H - 4} fontSize={10} fill="var(--color-ink-3)" textAnchor="end">{t[t.length - 1].toFixed(1)} s</text>
        {hover !== null && <text x={x(hover)} y={H - 4} fontSize={10} fill="var(--color-ink)" textAnchor="middle">{t[hover].toFixed(1)} s</text>}
      </svg>
    </div>
  )
}
