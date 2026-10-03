interface SparklineProps { values: number[]; tone: 'healthy' | 'critical' | 'neutral' }

export function Sparkline({ values, tone }: SparklineProps) {
  const width = 90
  const height = 30
  const series = values.length >= 2 ? values : [values[0] ?? 0, values[0] ?? 0]
  const max = Math.max(...series)
  const min = Math.min(...series)
  const points = series.map((value, index) => {
    const x = (index / (series.length - 1)) * width
    const y = height - ((value - min) / Math.max(max - min, 0.0001)) * (height - 5) - 2
    return `${x.toFixed(1)},${(max === min ? height / 2 : y).toFixed(1)}`
  }).join(' ')
  const stroke = tone === 'critical' ? '#ff7779' : tone === 'neutral' ? '#6f8193' : '#53cfad'
  return <svg className="sparkline" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${tone} trend`}><polyline points={points} fill="none" stroke={stroke} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
}
