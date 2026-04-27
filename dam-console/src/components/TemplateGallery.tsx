import type { TemplatePreset } from '@/lib/templates'

interface Props {
  readonly templates: TemplatePreset[]
  readonly selected: string
  readonly onSelect: (id: string) => void
}

const BADGE_COLOR: Record<string, string> = {
  LeRobot: 'bg-dam-blue-dim text-dam-blue border-blue-800',
  ROS2:    'bg-blue-950 text-dam-blue border-blue-800',
  Sim:     'bg-dam-surface-3 text-dam-muted border-dam-border',
}

export function TemplateGallery({ templates, selected, onSelect }: Props) {
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      {templates.map(t => {
        const active = selected === t.id
        return (
          <button
            key={t.id}
            onClick={() => onSelect(t.id)}
            className={`text-left p-4 rounded-xl border-2 transition-all ${
              active
                ? 'border-dam-blue bg-dam-blue-dim'
                : 'border-dam-border bg-dam-surface-2 hover:border-dam-blue/50'
            }`}
          >
            <div className="flex items-start justify-between mb-2">
              <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold border uppercase ${BADGE_COLOR[t.badge] ?? BADGE_COLOR['Sim']}`}>
                {t.badge}
              </span>
              {active && (
                <span className="text-dam-blue text-xs">✓</span>
              )}
            </div>
            <p className={`text-sm font-bold mb-1 ${active ? 'text-dam-blue' : 'text-dam-text'}`}>
              {t.label}
            </p>
            <p className="text-dam-muted text-[11px] leading-snug">{t.description}</p>
          </button>
        )
      })}
    </div>
  )
}
