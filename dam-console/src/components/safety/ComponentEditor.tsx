'use client'

import { useState } from 'react'
import { RegionEditor } from './RegionEditor'
import { safetyButton, safetyInput, type SafetyComponent, type ComponentKind, type SafetyNode, type SafetyContainer } from '@/lib/safety'

export const RULES: Record<string, { label: string; params: Record<string, unknown> }> = {
  joint_velocity_limit: { label: 'Joint speed (rad/s)', params: { max_velocities: 0.5 } },
  joint_acceleration_limit: { label: 'Joint acceleration (rad/s²)', params: { max_acceleration: 1 } },
  ee_velocity_limit: { label: 'Global end-effector speed (m/s)', params: { max_ee_velocity: 0.1 } },
  workspace: { label: 'Workspace box (m)', params: { bounds: [[-0.4, 0.4], [-0.4, 0.4], [0.02, 0.6]] } },
  keep_out_zone: { label: 'Keep-out sphere (m)', params: { spheres: [[0.2, 0, 0.2, 0.1]] } },
}

export function ComponentEditor({ component, onChange, onRemove, raw, onRawChange, frame }: {
  component: SafetyComponent
  onChange: (component: SafetyComponent) => void
  onRemove: () => void
  raw: string | null
  onRawChange: (raw: string | null) => void
  frame?: string
}) {
  const [rule, setRule] = useState('joint_velocity_limit')
  const [error, setError] = useState('')
  const updateNode = (boundary: string, index: number, node: SafetyNode) => {
    const container = component.boundaries[boundary]
    onChange({ ...component, boundaries: { ...component.boundaries, [boundary]: { ...container, nodes: container.nodes.map((n, i) => i === index ? node : n) } } })
  }
  return <article className="rounded-lg border border-dam-border bg-dam-surface p-4 space-y-4">
    <div className="flex gap-2 items-end">
      <label className="flex-1 text-xs text-dam-muted">Component name<input aria-label="Component name" className={safetyInput} value={component.name} onChange={event => onChange({ ...component, name: event.target.value })} /></label>
      <label className="text-xs text-dam-muted">Layer<select aria-label="Component kind" className={safetyInput} value={component.kind} onChange={event => onChange({ ...component, kind: event.target.value as ComponentKind })}>{['baseline', 'scene', 'task', 'mode'].map(kind => <option key={kind}>{kind}</option>)}</select></label>
      <button type="button" className={safetyButton} onClick={onRemove}>Remove component</button>
    </div>
    {component.kind === 'baseline' && <p className="text-xs text-dam-blue">Baseline rules are compiled into always-active protection. Conflicting definitions are rejected.</p>}
    <fieldset disabled={raw !== null} className={`space-y-3 disabled:opacity-50 ${raw !== null ? 'pointer-events-none' : ''}`}>
      {Object.entries(component.boundaries).map(([name, boundary]) => <section key={name} className="space-y-2">
        <div className="flex items-center justify-between gap-2"><h3 className="text-xs font-semibold break-all">{name} · {boundary.layer}</h3><button className={safetyButton} type="button" onClick={() => {
          const next = { ...component.boundaries }; delete next[name]; onChange({ ...component, boundaries: next })
        }}>Remove rule</button></div>
        {boundary.nodes.map((node, index) => <div key={index} className="space-y-2">
          <p className="text-xs text-dam-muted">{node.callback}</p>
          {(node.callback === 'workspace' || node.callback === 'keep_out_zone') && <>
            <p className="text-xs text-dam-muted">Frame: {frame || 'not declared — set the native solver frame above'}</p>
            <RegionEditor node={node} onChange={next => updateNode(name, index, next)} />
          </>}
          {Object.entries(node.params).filter(([, value]) => typeof value === 'number').map(([key, value]) => <label key={key} className="block text-xs text-dam-muted">{key}<input type="number" step="0.01" aria-label={`${name} ${key}`} className={safetyInput} value={value as number} onChange={event => {
            const n = event.currentTarget.valueAsNumber
            if (Number.isFinite(n)) updateNode(name, index, { ...node, params: { ...node.params, [key]: n } })
          }} /></label>)}
        </div>)}
      </section>)}
      <div className="flex gap-2"><select className={safetyInput} aria-label="New rule" value={rule} onChange={event => setRule(event.target.value)}>{Object.entries(RULES).map(([key, definition]) => <option key={key} value={key}>{definition.label}</option>)}</select><button type="button" className={`${safetyButton} whitespace-nowrap`} onClick={() => {
        let name = `${component.name.replace(/[^a-zA-Z0-9_]/g, '_')}_${rule}`
        while (component.boundaries[name]) name += '_new'
        onChange({ ...component, boundaries: { ...component.boundaries, [name]: { layer: 'L1', type: 'single', nodes: [{ callback: rule, params: JSON.parse(JSON.stringify(RULES[rule].params)) }] } } })
      }}>Add rule</button></div>
    </fieldset>
    <details open={raw !== null || undefined}><summary className="cursor-pointer text-xs text-dam-muted">Advanced boundary JSON</summary>
      <textarea aria-label={`Boundary JSON ${component.name}`} className={`${safetyInput} mt-2 font-mono text-xs`} rows={8} value={raw ?? JSON.stringify(component.boundaries, null, 2)} onChange={event => { onRawChange(event.target.value); setError('') }} />
      {raw !== null && <div className="flex gap-2 mt-2">
        <button type="button" className={safetyButton} onClick={() => {
          try {
            const parsed: unknown = JSON.parse(raw)
            if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object' || !Object.values(parsed).every(container => container && typeof container === 'object' && typeof container.layer === 'string' && typeof container.type === 'string' && Array.isArray(container.nodes) && container.nodes.every((node: unknown) => node && typeof node === 'object' && 'callback' in node && (node.callback === null || typeof node.callback === 'string') && 'params' in node && node.params && typeof node.params === 'object' && !Array.isArray(node.params)))) throw new Error('Expected named containers with layer, type and nodes containing params objects.')
            onChange({ ...component, boundaries: parsed as Record<string, SafetyContainer> }); onRawChange(null); setError('')
          } catch (caught) { setError(caught instanceof Error ? caught.message : 'Invalid JSON') }
        }}>Use edited JSON</button>
        <button type="button" className={safetyButton} onClick={() => { onRawChange(null); setError('') }}>Discard JSON edits</button>
      </div>}
      {error && <p role="alert" className="text-dam-red text-xs mt-2">{error}</p>}
    </details>
  </article>
}
