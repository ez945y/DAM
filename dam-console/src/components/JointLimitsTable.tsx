'use client'
import { Plus, X } from 'lucide-react'
import type { JointDef } from '@/lib/types'

interface Props {
  readonly joints: JointDef[]
  readonly onChange: (joints: JointDef[]) => void
}

export function JointLimitsTable({ joints, onChange }: Props) {
  const update = (i: number, field: keyof JointDef, value: string | number) => {
    const next = joints.map((j, idx) =>
      idx === i ? { ...j, [field]: value } : j
    )
    onChange(next)
  }

  const addJoint = () => {
    onChange([
      ...joints,
      {
        name: `joint_${joints.length + 1}`,
        lower_rad: -2,
        upper_rad: 2,
      },
    ])
  }

  const removeJoint = (i: number) => {
    onChange(joints.filter((_, idx) => idx !== i))
  }

  const inputCls =
    'w-full bg-dam-surface-3 border border-dam-border rounded px-1.5 py-0.5 text-dam-text font-mono text-xs'

  return (
    <div className="space-y-3">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-dam-border">
              <th className="text-left px-2 py-1.5 text-dam-muted">Name</th>
              <th className="text-left px-2 py-1.5 text-dam-muted">Lower [rad]</th>
              <th className="text-left px-2 py-1.5 text-dam-muted">Upper [rad]</th>
              <th className="px-2 py-1.5" />
            </tr>
          </thead>
          <tbody>
            {joints.map((j, i) => (
              <tr key={j.name || i} className="border-b border-dam-border/40">
                <td className="px-2 py-1">
                  <input
                    type="text"
                    value={j.name}
                    onChange={e => update(i, 'name', e.target.value)}
                    placeholder={`joint_${i + 1}`}
                    className={inputCls}
                  />
                </td>
                <td className="px-2 py-1">
                  <input
                    type="number"
                    step="0.01"
                    value={j.lower_rad}
                    onChange={e => update(i, 'lower_rad', Number(e.target.value))}
                    className={inputCls}
                  />
                </td>
                <td className="px-2 py-1">
                  <input
                    type="number"
                    step="0.01"
                    value={j.upper_rad}
                    onChange={e => update(i, 'upper_rad', Number(e.target.value))}
                    className={inputCls}
                  />
                </td>
                <td className="px-2 py-1">
                  <button
                    onClick={() => removeJoint(i)}
                    className="text-dam-muted hover:text-dam-red transition-colors"
                    title="Remove joint"
                  >
                    <X size={13} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <button
        onClick={addJoint}
        className="flex items-center gap-1.5 text-xs text-dam-muted hover:text-dam-blue transition-colors"
      >
        <Plus size={12} /> Add joint
      </button>
    </div>
  )
}
