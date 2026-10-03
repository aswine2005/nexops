import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { clock } from '../lib/format'

interface ErrorRateChartProps {
  data: Array<{ t: string; value: number | null }>
  markers?: Array<{ t: string; label: string }>
  baseline?: number | null
  healthy?: boolean
}

export function ErrorRateChart({ data, markers = [], baseline, healthy = true }: ErrorRateChartProps) {
  const rows = data.map((point) => ({ ts: new Date(point.t).getTime(), value: point.value }))
  const high = Math.max(1, ...rows.map((row) => row.value ?? 0)) * 1.15
  const color = healthy ? '#58c8a3' : '#ff6f70'
  const first = rows[0]?.ts ?? 0
  const last = rows[rows.length - 1]?.ts ?? 0
  return (
    <div className="chart-wrap">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={rows} margin={{ top: 16, right: 24, left: -6, bottom: 0 }}>
          <defs><linearGradient id="errorFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={color} stopOpacity={0.24} /><stop offset="95%" stopColor={color} stopOpacity={0} /></linearGradient></defs>
          <CartesianGrid stroke="#202a35" strokeDasharray="3 5" vertical={false} />
          <XAxis dataKey="ts" type="number" scale="time" domain={['dataMin', 'dataMax']} tickFormatter={(value: number) => clock(new Date(value).toISOString())} tick={{ fill: '#9aa8b8', fontSize: 13, fontFamily: 'DM Mono' }} axisLine={false} tickLine={false} minTickGap={28} dy={10} />
          <YAxis tick={{ fill: '#9aa8b8', fontSize: 13, fontFamily: 'DM Mono' }} axisLine={false} tickLine={false} tickFormatter={(value: number) => `${value.toFixed(value < 1 ? 1 : 0)}%`} domain={[0, high]} />
          <Tooltip content={<ChartTooltip />} cursor={{ stroke: '#43566a', strokeDasharray: '3 4' }} />
          {markers.map((marker) => { const x = new Date(marker.t).getTime(); return x >= first && x <= last ? <ReferenceLine key={`${marker.t}-${marker.label}`} x={x} stroke="#51a9ff" strokeDasharray="4 4" strokeWidth={1.2} label={{ value: marker.label, position: 'insideTopLeft', fill: '#91cbff', fontSize: 13, fontFamily: 'DM Mono' }} /> : null })}
          {baseline !== null && baseline !== undefined && <ReferenceLine y={baseline} stroke="#718092" strokeDasharray="5 5" strokeWidth={1} />}
          <Area type="monotone" dataKey="value" name="Error rate" stroke={color} strokeWidth={2.2} fill="url(#errorFill)" connectNulls isAnimationActive={false} activeDot={{ r: 4, stroke: color, strokeWidth: 2, fill: '#111720' }} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

function ChartTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ value: number }>; label?: number }) {
  if (!active || !payload?.length || payload[0].value === null || payload[0].value === undefined) return null
  return <div className="chart-tooltip"><span>{label ? clock(new Date(label).toISOString(), true) : ''}</span><strong>{payload[0].value.toFixed(2)}%</strong><small>HTTP 5xx error rate</small></div>
}
