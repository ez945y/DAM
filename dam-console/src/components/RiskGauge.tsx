'use client'
import type { LucideProps } from 'lucide-react'
import { ShieldCheck, ShieldAlert, ShieldX, Zap } from 'lucide-react'
import type { RiskLevel } from '@/lib/types'

interface LevelConfig {
  stroke: string
  text: string
  bg: string
  border: string
  glow: string
  pct: number            // 0.0 – 1.0 arc fill
  Icon: React.ComponentType<LucideProps>
  glowClass: string
}

const CONFIG: Record<RiskLevel, LevelConfig> = {
  NORMAL:    { stroke: '#22C55E', text: 'text-dam-green',  bg: 'bg-[#071a0e]', border: 'border-green-800/60',  glow: '#22C55E', pct: 0.08, Icon: ShieldCheck, glowClass: 'animate-glow-green'  },
  ELEVATED:  { stroke: '#3B82F6', text: 'text-dam-blue',   bg: 'bg-dam-blue-dim', border: 'border-blue-800/60',   glow: '#3B82F6', pct: 0.40, Icon: ShieldAlert, glowClass: 'animate-glow-blue' },
  CRITICAL:  { stroke: '#F97316', text: 'text-dam-orange', bg: 'bg-[#1a0d00]', border: 'border-orange-800/60', glow: '#F97316', pct: 0.72, Icon: ShieldX,     glowClass: 'animate-glow-orange' },
  EMERGENCY: { stroke: '#EF4444', text: 'text-dam-red',    bg: 'bg-[#1a0505]', border: 'border-red-800/60',    glow: '#EF4444', pct: 1.00, Icon: Zap,         glowClass: 'animate-glow-red'    },
}

const R = 52
const CIRCUMFERENCE = 2 * Math.PI * R   // ≈ 326.7

export function RiskGauge({ level }: { level: RiskLevel }) {
  const { stroke, text, pct, Icon, glowClass } = CONFIG[level]
  const filled    = CIRCUMFERENCE * pct
  const remaining = CIRCUMFERENCE - filled

  return (
    <div className={`panel bg-dam-surface-2/50 border-dam-border/60 p-5 overflow-hidden relative ${glowClass}`}>
      <p className="section-label mb-3">Risk Level</p>
      <div className="flex items-center gap-5">
        {/* SVG Arc Gauge */}
        <div className="relative shrink-0" style={{ width: 96, height: 96 }}>
          <svg viewBox="0 0 120 120" width={96} height={96}>
            {/* Decorative outer dashed ring */}
            <circle cx="60" cy="60" r="57" fill="none" stroke="rgba(255,255,255,0.04)" strokeWidth="1" strokeDasharray="3 5" />
            {/* Track */}
            <circle cx="60" cy="60" r={R} fill="none" stroke="rgba(255,255,255,0.07)" strokeWidth="10" />
            {/* Filled arc */}
            <circle
              cx="60" cy="60" r={R}
              fill="none"
              stroke={stroke}
              strokeWidth="10"
              strokeLinecap="round"
              strokeDasharray={`${filled} ${remaining}`}
              strokeDashoffset={CIRCUMFERENCE * 0.25}
              className="animate-dash-in"
              style={{ filter: `drop-shadow(0 0 6px ${stroke}88)` }}
            />
            {/* Centre icon */}
            <foreignObject x="35" y="35" width="50" height="50">
              <div className="flex items-center justify-center w-full h-full">
                <Icon size={28} className={text} />
              </div>
            </foreignObject>
          </svg>
        </div>

        {/* Text */}
        <div className="min-w-0">
          <p className={`text-2xl font-black tracking-wide leading-none ${text}`}>{level}</p>
          <p className="text-dam-muted text-xs mt-1.5 leading-snug">
            {level === 'NORMAL'    && 'All guards passing'}
            {level === 'ELEVATED'  && 'Minor violations detected'}
            {level === 'CRITICAL'  && 'Repeated rejections'}
            {level === 'EMERGENCY' && 'E-Stop triggered'}
          </p>
        </div>
      </div>
    </div>
  )
}
