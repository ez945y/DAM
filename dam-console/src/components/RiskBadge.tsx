import type { RiskLevel } from '@/lib/types'

const RISK_STYLES: Record<RiskLevel, string> = {
  NORMAL:    'bg-green-950 text-dam-green border border-green-800',
  ELEVATED:  'bg-blue-950 text-dam-blue border border-blue-800',
  CRITICAL:  'bg-orange-950 text-dam-orange border border-orange-800',
  EMERGENCY: 'bg-red-950 text-dam-red border border-red-800 animate-pulse-red',
}

export function RiskBadge({ level, size = 'sm' }: { level: RiskLevel; size?: 'sm' | 'lg' }) {
  const base = size === 'lg'
    ? 'px-4 py-1.5 text-base font-bold rounded-md'
    : 'px-2 py-0.5 text-xs font-semibold rounded'
  return (
    <span className={`${base} ${RISK_STYLES[level]}`}>
      {level}
    </span>
  )
}
