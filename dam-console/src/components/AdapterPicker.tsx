'use client'
import type { PolicyConfig } from '@/lib/types'

interface AdapterOption<T extends string> {
  id: T
  label: string
  description: string
  extra?: string
}

export type AdapterType = 'lerobot' | 'ros2' | 'simulation'
export type PolicyTargetType = PolicyConfig['type']

export const ADAPTERS: AdapterOption<AdapterType>[] = [
  { id: 'lerobot',    label: 'LeRobot',    description: 'SO-ARM101 / Koch v1.1 via lerobot', extra: 'lerobot' },
  { id: 'ros2',       label: 'ROS2',       description: 'JointState subscriber via rclpy',   extra: 'ros2'    },
  { id: 'simulation', label: 'Simulation', description: 'Synthetic sensor — no hardware'                      },
]

export const POLICIES: AdapterOption<PolicyTargetType>[] = [
  { id: 'act',       label: 'ACT',             description: 'Action Chunking with Transformers',   extra: 'torch' },
  { id: 'diffusion', label: 'Diffusion Policy', description: 'DDPM / DDIM policy (needs GPU)',      extra: 'torch' },
  { id: 'smolvla',   label: 'SmolVLA',          description: 'Small vision-language-action model',  extra: 'torch' },
]

export function AdapterColumn<T extends string>({
  title,
  options,
  selected,
  onSelect,
}: {
  title: string
  options: AdapterOption<T>[]
  selected: T
  onSelect: (id: T) => void
}) {
  return (
    <div>
      <h3 className="text-dam-muted text-[10px] uppercase tracking-widest mb-2">{title}</h3>
      <div className="space-y-1.5">
        {options.map(opt => {
          const active = selected === opt.id
          return (
            <button
              key={opt.id}
              onClick={() => onSelect(opt.id)}
              className={`w-full text-left px-3 py-2.5 rounded-lg border transition-all ${
                active
                  ? 'border-dam-blue bg-dam-blue-dim'
                  : 'border-dam-border bg-dam-surface-2 hover:border-dam-blue/40'
              }`}
            >
              <div className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full shrink-0 ${active ? 'bg-dam-blue' : 'bg-dam-surface-3'}`} />
                <span className={`text-sm font-semibold ${active ? 'text-dam-blue' : 'text-dam-text'}`}>
                  {opt.label}
                </span>
                {opt.extra && (
                  <span className="ml-auto text-[9px] px-1 py-0.5 rounded bg-dam-surface-3 text-dam-muted border border-dam-border font-mono">
                    [{opt.extra}]
                  </span>
                )}
              </div>
              <p className="text-dam-muted text-[11px] mt-0.5 ml-4">{opt.description}</p>
            </button>
          )
        })}
      </div>
    </div>
  )
}

interface Props {
  adapter: 'lerobot' | 'ros2' | 'simulation'
  policy: PolicyConfig['type']
  onChange: (field: 'adapter' | 'policy', value: string) => void
}

export function AdapterPicker({ adapter, policy, onChange }: Props) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      <AdapterColumn
        title="Hardware (Source + Sink)"
        options={ADAPTERS}
        selected={adapter}
        onSelect={v => onChange('adapter', v)}
      />
      <AdapterColumn
        title="Policy (Brain)"
        options={POLICIES}
        selected={policy}
        onSelect={v => onChange('policy', v)}
      />
    </div>
  )
}
