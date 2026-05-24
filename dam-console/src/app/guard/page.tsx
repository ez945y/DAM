'use client'
import { useState, useRef, useEffect, useCallback } from 'react'
import {
  Plus, Trash2, ShieldCheck,
  ToggleLeft, ToggleRight, ChevronDown, ChevronRight,
  ChevronLeft, List, Layout, LayoutDashboard, Layers, Check, X,
  Database, Info
} from 'lucide-react'
import { ActionShell } from '@/components/ActionShell'
import type {
  TaskDef,
  BoundaryDef,
  ConstraintNodeDef,
  BoundaryCallbackDef,
  BoundaryCallbackGroupDef,
  BoundaryTemplateDef,
  FallbackDef,
} from '@/lib/types'
import { DamConfig, defaultConfig, generateYaml, parseConfigFromYaml } from '@/lib/templates'
import { OODTrainer } from '@/components/OODTrainer'
import { useStackfileLibrary } from '@/hooks/useStackfileLibrary'
import { StackfileLibraryBar } from '@/components/StackfileLibraryBar'
import { api } from '@/lib/api'

// ── Guard pipeline definitions ───────────────────────────────────────────────

interface GuardDef {
  readonly id: string
  readonly name: string
  readonly layer: string
  readonly description: string
}


const LAYER_COLORS: { [key: string]: string } = {
  L0: 'text-purple-400 border-purple-400/30 bg-purple-400/10',
  L1: 'text-dam-blue border-dam-blue/30 bg-dam-blue/10',
  L2: 'text-dam-orange border-dam-orange/30 bg-dam-orange/10',
  L3: 'text-dam-red border-dam-red/30 bg-dam-red/10',
}

// ── Factory helpers ───────────────────────────────────────────────────────────

function makeTask(existingBoundaries: string[] = []): TaskDef {
  return { id: crypto.randomUUID(), name: 'new_task', description: '', boundaries: existingBoundaries }
}

function makeNode(): ConstraintNodeDef {
  return {
    node_id: '',
    params: {},
    callback: null,
    fallback: 'emergency_stop',
    timeout_sec: null,
  }
}

function uniqueBoundaryName(base: string, existingNames: Set<string>): string {
  const cleanBase = base.trim().toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '') || 'boundary'
  if (!existingNames.has(cleanBase)) return cleanBase
  let idx = 2
  while (existingNames.has(`${cleanBase}_${idx}`)) idx += 1
  return `${cleanBase}_${idx}`
}

function isAutoBoundaryName(name: string, layer: string): boolean {
  const lower = name.trim().toLowerCase()
  return !lower || lower === `${layer.toLowerCase()}_boundary` || /^l[0-3]_boundary_\d+$/.test(lower)
}

function makeBoundary(layerStr = 'L1'): BoundaryDef {
  return { name: '', layer: layerStr, type: 'single', nodes: [makeNode()] }
}

function materializeBoundaryTemplate(
  template: BoundaryTemplateDef,
  existingNames: Set<string>,
): BoundaryDef {
  return {
    ...template.boundary,
    name: uniqueBoundaryName(template.boundary.name || template.name, existingNames),
    nodes: template.boundary.nodes.map(node => ({
      ...node,
      params: { ...(node.params || {}) },
    })),
  }
}

function inferGripperCommand(node: ConstraintNodeDef): 'close' | 'open' | 'none' {
  const value = node.params?.allowed_command
  if (value === 'close' || value === 'open' || value === 'none') return value
  const id = node.node_id.toLowerCase()
  if (id.includes('pick')) return 'close'
  if (id.includes('place')) return 'open'
  return 'none'
}

function defaultParamsForCallback(callback: string | null, meta?: BoundaryCallbackDef): Record<string, any> {
  if (callback === 'task_gripper_command_guard') return { allowed_command: 'none' }
  const params: Record<string, any> = {}
  if (!meta?.params) return params
  Object.entries(meta.params).forEach(([pName, pMeta]) => {
    if (pMeta.has_default && pMeta.default != null) params[pName] = pMeta.default
  })
  return params
}

function normalizeGripperNode(node: ConstraintNodeDef): ConstraintNodeDef {
  if (node.callback !== 'task_gripper_command_guard') return node
  const allowed = inferGripperCommand(node)
  const nextParams: Record<string, unknown> = { allowed_command: allowed }
  if (allowed !== 'none' && node.params?.zone) nextParams.zone = node.params.zone
  return { ...node, params: nextParams }
}

function fallbackTitle(fallback: FallbackDef): string {
  const parts = [
    fallback.description,
    fallback.severity != null ? `severity ${fallback.severity}` : null,
    fallback.requires_proposal != null ? `proposal ${fallback.requires_proposal ? 'yes' : 'no'}` : null,
    fallback.monitors_hardware != null ? `hardware monitor ${fallback.monitors_hardware ? 'yes' : 'no'}` : null,
  ].filter(Boolean)
  return parts.join(' | ')
}

// ── Shared input class ────────────────────────────────────────────────────────

const inputCls =
  'bg-dam-surface-2 border border-dam-border rounded px-2 py-1.5 text-xs font-mono text-dam-text focus:outline-none focus:border-dam-blue/60 transition-colors'



function NodeForm({
  node,
  index,
  isActive,
  onChange,
  onRemove,
  callbackCatalog = [],
  callbackGroups = [],
  fallbackCatalog = [],
  allowNodeIdEdit,
  boundaryName,
  boundaryLayer,
  onBoundaryNameChange,
  existingBoundaryNames = new Set<string>(),
}: {
  node: ConstraintNodeDef
  index: number
  isActive?: boolean
  onChange: (n: ConstraintNodeDef) => void
  onRemove: () => void
  callbackCatalog?: BoundaryCallbackDef[]
  callbackGroups?: BoundaryCallbackGroupDef[]
  fallbackCatalog?: FallbackDef[]
  allowNodeIdEdit?: boolean
  boundaryName?: string
  boundaryLayer?: string
  onBoundaryNameChange?: (name: string) => void
  existingBoundaryNames?: Set<string>
}) {
  // Auto-initialize fields if they are missing but expected by the boundary name
  // This helps when a user clicks 'Add Boundary' but the node is empty
  useEffect(() => {
    let changed = false
    const currentParams = node.params || {}
    const nextParams = { ...currentParams }

    if (node.callback === 'joint_position_limits' && (!nextParams.upper || !nextParams.lower)) {
      nextParams.upper = [1.82, 1.77, 1.60, 1.81, 3.07, 1.75]
      nextParams.lower = [-1.82, -1.77, -1.60, -1.81, -3.07, 0]
      nextParams.use_degrees = false
      changed = true
    }
    if (node.callback === 'joint_velocity_limit' && !nextParams.max_velocities) {
      nextParams.max_velocities = [1.5, 1.5, 1.5, 1.5, 1.5, 1.5]
      nextParams.use_degrees = false
      changed = true
    }
    if (node.callback === 'workspace' && !nextParams.bounds) {
      nextParams.bounds = [[-0.4, 0.4], [-0.4, 0.4], [0.02, 0.6]]
      changed = true
    }

    if (changed) onChange({ ...node, params: nextParams })
  }, [node, onChange])

  const bounds = node.params?.bounds as [[number,number],[number,number],[number,number]] | null
  const joint_position_limits = (node.params?.upper && node.params?.lower) ? { upper: node.params.upper as number[], lower: node.params.lower as number[] } : null
  const hasBounds = !!bounds
  const isOod = !!node.callback?.startsWith('ood_')
  const callbackParamMeta = callbackCatalog.find(c => c.name === node.callback)?.params ?? {}
  const paramTitle = (name: string) => callbackParamMeta[name]?.description || undefined
  const paramLabelCls = (name: string) =>
    `text-dam-muted cursor-help ${paramTitle(name) ? 'underline decoration-dotted underline-offset-2' : ''}`

  const updateBound = (axis: 0 | 1 | 2, side: 0 | 1, val: number) => {
    if (!bounds) return
    const next = bounds.map((pair, i) =>
      i === axis ? ([...pair] as [number, number]).map((v, j) => j === side ? val : v) as [number, number] : pair
    ) as [[number, number], [number, number], [number, number]]
    onChange({ ...node, params: { ...node.params, bounds: next } })
  }

  return (
    <div className={`p-2.5 rounded bg-dam-surface-3 border transition-colors space-y-2 text-xs ${
      isActive ? 'border-dam-blue/50' : 'border-dam-border/60'
    }`}>
      <div className={`flex items-center gap-2 ${(!allowNodeIdEdit || isOod) ? 'hidden' : ''}`}>
        <div className="flex flex-col flex-1 gap-1">
          <label htmlFor={`node-${index}-id`} className="text-dam-muted text-[9px] uppercase font-bold tracking-tight">Internal Node ID</label>
          <input
            id={`node-${index}-id`}
            value={node.node_id}
            onChange={e => onChange({ ...node, node_id: e.target.value })}
            placeholder="e.g., primary_check"
            className={`w-full ${inputCls}`}
          />
        </div>
        <button onClick={onRemove} className="text-dam-muted hover:text-dam-red transition-colors pt-4 px-1 shrink-0">
          <Trash2 size={12} />
        </button>
      </div>
      <div className="grid grid-cols-4 gap-2">
        <div className="space-y-0.5 col-span-2">
          <label htmlFor={`node-${index}-callback`} className="text-dam-muted text-[10px]">Callback Template</label>
          <select
            id={`node-${index}-callback`}
            value={node.callback || ''}
            onChange={e => {
              const newCallback = e.target.value === '' ? null : e.target.value
              const meta = callbackCatalog.find(c => c.name === newCallback)
              const newParams = defaultParamsForCallback(newCallback, meta)
              const nextBoundaryName = newCallback && isAutoBoundaryName(boundaryName ?? '', boundaryLayer ?? '')
                ? uniqueBoundaryName(
                  newCallback,
                  new Set([...existingBoundaryNames].filter(n => n !== boundaryName))
                )
                : boundaryName
              if (nextBoundaryName && nextBoundaryName !== boundaryName) onBoundaryNameChange?.(nextBoundaryName)
              // Auto-assign node_id from boundary name (preferred) or callback name
              const nextId = (!node.node_id || node.node_id === 'default' || node.node_id === node.callback)
                ? (nextBoundaryName || newCallback || 'default')
                : node.node_id

              onChange(normalizeGripperNode({ ...node, node_id: nextId, callback: newCallback, params: newParams }))
            }}
            className={`w-full ${inputCls}`}
          >
            <option value="">None</option>
            {callbackGroups.length > 0 ? (
              callbackGroups.map(group => (
                <optgroup key={group.layer} label={`${group.layer} Layer`} className="bg-dam-surface-3 font-semibold text-dam-blue">
                  {group.callbacks.map(cb => (
                    <option key={cb.name} value={cb.name} className="bg-dam-surface-2 text-dam-text font-normal">
                      {cb.name}
                    </option>
                  ))}
                </optgroup>
              ))
            ) : (
              ['L0', 'L1', 'L2', 'L3'].map(layer => {
                const group = callbackCatalog.filter(c => c.layer === layer)
                if (group.length === 0) return null
                return (
                  <optgroup key={layer} label={`${layer} Layer`} className="bg-dam-surface-3 font-semibold text-dam-blue">
                    {group.map(cb => (
                      <option key={cb.name} value={cb.name} className="bg-dam-surface-2 text-dam-text font-normal">
                        {cb.name}
                      </option>
                    ))}
                  </optgroup>
                )
              })
            )}
          </select>
        </div>
        <div className="space-y-0.5">
          <label htmlFor={`node-${index}-fallback`} className="text-dam-muted text-[10px]">Fallback</label>
          <select
            id={`node-${index}-fallback`}
            value={node.fallback ?? ''}
            onChange={e => onChange({ ...node, fallback: e.target.value || null })}
            className={`w-full ${inputCls}`}
          >
            {fallbackCatalog.map(f => (
              <option key={f.name} value={f.name} title={fallbackTitle(f)}>{f.name}</option>
            ))}
          </select>
          {(() => {
            const selected = fallbackCatalog.find(f => f.name === node.fallback)
            if (!selected) return null
            const bits = [
              selected.severity != null ? `sev ${selected.severity}` : null,
              selected.monitors_hardware != null ? `hw ${selected.monitors_hardware ? 'on' : 'off'}` : null,
            ].filter(Boolean)
            return bits.length > 0 ? (
              <p className="text-[9px] text-dam-muted/70 font-mono truncate" title={fallbackTitle(selected)}>
                {bits.join(' · ')}
              </p>
            ) : null
          })()}
        </div>
        <div className="space-y-0.5">
          <label htmlFor={`node-${index}-timeout`} className="text-dam-muted text-[10px]">Timeout (sec)</label>
          <input
            id={`node-${index}-timeout`}
            type="number"
            step="0.5"
            value={node.timeout_sec ?? ''}
            onChange={e => onChange({ ...node, timeout_sec: e.target.value === '' ? null : Number(e.target.value) })}
            placeholder="none"
            className={`w-full ${inputCls}`}
          />
        </div>
      </div>
      {/* Standard toggles removed as per user request — fields show based on node content */}
      <div className="pt-1" />

      {hasBounds && bounds && (
        <div className="grid grid-cols-3 gap-1.5 text-[10px]">
          {(['X', 'Y', 'Z'] as const).map((axis, axisIdx) => (
            <div key={axis} className="space-y-0.5">
              <label
                htmlFor={`node-${index}-bound-${axis}-min`}
                className={paramLabelCls('bounds')}
                title={paramTitle('bounds')}
              >
                {axis} [min, max]
              </label>
              <div className="flex gap-1">
                <input
                  id={`node-${index}-bound-${axis}-min`}
                  type="number"
                  step="0.05"
                  value={bounds![axisIdx][0]}
                  onChange={e => updateBound(axisIdx as 0|1|2, 0, Number(e.target.value))}
                  className={`w-full ${inputCls}`}
                />
                <input
                  type="number"
                  step="0.05"
                  value={bounds![axisIdx][1]}
                  onChange={e => updateBound(axisIdx as 0|1|2, 1, Number(e.target.value))}
                  className={`w-full ${inputCls}`}
                />
              </div>
            </div>
          ))}
        </div>
      )}

      {joint_position_limits && Array.isArray(joint_position_limits.upper) && (
        <div className="space-y-1.5 border-t border-dam-border/40 pt-1.5">
          <div className="flex items-center justify-between">
            <p className="text-dam-muted text-[10px] uppercase font-bold tracking-widest">Joint Position Limits</p>
            <label
              className="flex items-center gap-1.5 text-[10px] text-dam-muted cursor-pointer hover:text-dam-blue transition-colors"
              title={paramTitle('use_degrees')}
            >
              <input
                type="checkbox"
                checked={!!node.params.use_degrees}
                onChange={e => {
                  const nextParams = { ...node.params }
                  nextParams.use_degrees = e.target.checked
                  onChange({ ...node, params: nextParams })
                }}
                className="w-3 h-3 rounded border-dam-border bg-dam-surface-2 text-dam-blue focus:ring-0 focus:ring-offset-0"
              />
              <span className={paramTitle('use_degrees') ? 'underline decoration-dotted underline-offset-2' : ''}>use_degrees</span>
            </label>
          </div>
          <div className="grid grid-cols-6 gap-1">
            {joint_position_limits.upper.map((_: number, i: number) => (
              <div key={`joint-pos-${i}`} className="space-y-0.5">
                <label
                  htmlFor={`node-${index}-upper-${i}`}
                  className={`text-[8px] block ${paramLabelCls('upper')}`}
                  title={`${paramTitle('lower') || ''}${paramTitle('lower') && paramTitle('upper') ? '\n' : ''}${paramTitle('upper') || ''}` || undefined}
                >
                  J{i+1} [lo, hi]
                </label>
                <div className="flex flex-col gap-0.5">
                  <input
                    id={`node-${index}-upper-${i}`}
                    type="number"
                    step="0.1"
                    value={joint_position_limits!.upper[i]}
                    onChange={e => {
                      const nextUpper = [...(node.params.upper as number[])]
                      nextUpper[i] = Number(e.target.value)
                      onChange({ ...node, params: { ...node.params, upper: nextUpper } })
                    }}
                    className={`w-full ${inputCls}`}
                  />
                  <input
                    type="number"
                    step="0.1"
                    value={joint_position_limits!.lower[i]}
                    onChange={e => {
                      const nextLower = [...(node.params.lower as number[])]
                      nextLower[i] = Number(e.target.value)
                      onChange({ ...node, params: { ...node.params, lower: nextLower } })
                    }}
                    className={`w-full ${inputCls}`}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {node.callback === 'joint_velocity_limit' && node.params.max_velocities && Array.isArray(node.params.max_velocities) && (
        <div className="space-y-1.5 border-t border-dam-border/40 pt-1.5">
          <div className="flex items-center justify-between">
            <p className="text-dam-muted text-[10px] uppercase font-bold tracking-widest">Joint Velocity Limits</p>
            <label
              className="flex items-center gap-1.5 text-[10px] text-dam-muted cursor-pointer hover:text-dam-blue transition-colors"
              title={paramTitle('use_degrees')}
            >
              <input
                type="checkbox"
                checked={!!node.params.use_degrees}
                onChange={e => {
                  const nextParams = { ...node.params }
                  nextParams.use_degrees = e.target.checked
                  onChange({ ...node, params: nextParams })
                }}
                className="w-3 h-3 rounded border-dam-border bg-dam-surface-2 text-dam-blue focus:ring-0 focus:ring-offset-0"
              />
              <span className={paramTitle('use_degrees') ? 'underline decoration-dotted underline-offset-2' : ''}>use_degrees</span>
            </label>
          </div>
          <div className="grid grid-cols-6 gap-1">
            {(node.params.max_velocities as number[]).map((v, i) => (
              <div key={`joint-vel-${i}`} className="space-y-0.5">
                <label
                  htmlFor={`node-${index}-maxvel-${i}`}
                  className={`text-[8px] block ${paramLabelCls('max_velocities')}`}
                  title={paramTitle('max_velocities')}
                >
                  J{i+1} Max
                </label>
                <input
                  id={`node-${index}-maxvel-${i}`}
                  type="number"
                  step="0.1"
                  value={v}
                  onChange={e => {
                    const nextV = [...(node.params.max_velocities as number[])]
                    nextV[i] = Number(e.target.value)
                    onChange({ ...node, params: { ...node.params, max_velocities: nextV } })
                  }}
                  className={`w-full ${inputCls}`}
                />
              </div>
            ))}
          </div>
        </div>
      )}

      {node.callback === 'task_gripper_command_guard' && (
        <div className="space-y-2 border-t border-dam-border/40 pt-2">
          <p className="text-dam-muted text-[10px] uppercase font-bold tracking-widest">Gripper Gate</p>
          <div className="grid grid-cols-1 sm:grid-cols-[120px_1fr] gap-2">
            <label className="space-y-0.5">
              <span
                className={`text-[10px] ${paramLabelCls('allowed_command')}`}
                title={paramTitle('allowed_command')}
              >
                allowed_command
              </span>
              <select
                value={inferGripperCommand(node)}
                onChange={e => {
                  const allowed = e.target.value as 'close' | 'open' | 'none'
                  const nextParams: Record<string, unknown> = { allowed_command: allowed }
                  if (allowed !== 'none' && node.params.zone) nextParams.zone = node.params.zone
                  onChange({ ...node, params: nextParams })
                }}
                className={`w-full ${inputCls}`}
              >
                <option value="close">close</option>
                <option value="open">open</option>
                <option value="none">none</option>
              </select>
            </label>
            {inferGripperCommand(node) !== 'none' && (
              <label className="space-y-0.5">
                <span className={`text-[10px] ${paramLabelCls('zone')}`} title={paramTitle('zone')}>zone</span>
                <input
                  type="text"
                  value={node.params.zone == null ? '' : JSON.stringify(node.params.zone)}
                  placeholder="[[xmin,xmax],[ymin,ymax],[zmin,zmax]]"
                  onChange={e => {
                    const nextParams: Record<string, unknown> = {
                      allowed_command: inferGripperCommand(node),
                    }
                    try {
                      nextParams.zone = JSON.parse(e.target.value)
                    } catch {
                      nextParams.zone = e.target.value
                    }
                    onChange({ ...node, params: nextParams })
                  }}
                  className={`w-full ${inputCls}`}
                />
              </label>
            )}
          </div>
        </div>
      )}

      {/* Dynamic parameters for the selected callback */}
      {(() => {
        if (!node.callback) return null
        const meta = callbackCatalog.find(c => c.name === node.callback)
        if (!meta || !meta.params || Object.keys(meta.params).length === 0) return null

        // Specialized OOD UI
        if (isOod) {
          return (
            <div className="space-y-4 border-t border-dam-border/40 pt-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Database size={13} className="text-dam-blue" />
                  <p className="text-dam-muted text-[10px] uppercase font-bold tracking-widest">Neural Intelligence Profile</p>
                </div>
                <div className="flex gap-3 text-[10px] font-mono text-dam-muted/60">
                  <span className="flex items-center gap-1.5"><div className="w-1 h-1 rounded-full bg-dam-blue" /> Path Linked</span>
                </div>
              </div>

              <div className="bg-dam-surface-2/50 rounded-xl border border-dam-border/40 overflow-hidden">
                <OODTrainer
                  selectedPath={node.params.ood_model_path ?? ''}
                  onSelectMeta={(path, meta) => {
                    const next: Record<string, unknown> = { ...node.params, ood_model_path: path }
                    if (meta.bank_path) next.bank_path = meta.bank_path
                    if (meta.backend) next.backend = meta.backend
                    onChange({ ...node, params: next })
                  }}
                />
              </div>

        {/* OOD Parameters with tooltips */}
        <div className="grid grid-cols-2 gap-3 mt-4">
                 <div className="space-y-1">
                    <label
                      htmlFor={`node-${index}-nn-threshold`}
                      className="text-dam-muted text-[9px] uppercase font-bold tracking-tight px-1 flex justify-between cursor-help group"
                      title="Sensitivity (NN): Nearest Neighbor threshold. Measures the 'Similarity' between current state and memory bank samples. Range: 0.5 - 5.0. Lower is stricter."
                    >
                      <span className="flex items-center gap-1 underline decoration-dotted underline-offset-2 group-hover:text-dam-blue transition-colors">Sensitivity (NN) <Info size={8} /></span>
                      <span className="opacity-40 italic">0.5 - 5.0</span>
                    </label>
                    <input
                      id={`node-${index}-nn-threshold`}
                      type="number"
                      step="0.1"
                      value={node.params.nn_threshold ?? 2}
                      onChange={e => onChange({ ...node, params: { ...node.params, nn_threshold: Number(e.target.value) } })}
                      className={`w-full ${inputCls} h-9 rounded-lg`}
                    />
                 </div>
                 <div className="space-y-1">
                    <label
                      htmlFor={`node-${index}-nll-threshold`}
                      className="text-dam-muted text-[9px] uppercase font-bold tracking-tight px-1 flex justify-between cursor-help group"
                      title="Density (NLL): Negative Log-Likelihood threshold. Measures the 'Probability Floor'. Higher is more tolerant (allows low prob), lower is stricter. Range: 3.0 - 15.0."
                    >
                      <span className="flex items-center gap-1 underline decoration-dotted underline-offset-2 group-hover:text-dam-blue transition-colors">Density (NLL) <Info size={8} /></span>
                      <span className="opacity-40 italic">3.0 - 15.0</span>
                    </label>
                    <input
                      id={`node-${index}-nll-threshold`}
                      type="number"
                      step="0.5"
                      value={node.params.nll_threshold ?? 5}
                      onChange={e => onChange({ ...node, params: { ...node.params, nll_threshold: Number(e.target.value) } })}
                      className={`w-full ${inputCls} h-9 rounded-lg`}
                    />
                 </div>
                 <div className="space-y-1">
                    <label
                      htmlFor={`node-${index}-temporal-smoothing`}
                      className="text-dam-muted text-[9px] uppercase font-bold tracking-tight px-1 flex justify-between cursor-help group"
                      title="Temporal smoothing: require this many consecutive OOD frames before the boundary rejects. Use this to absorb lighting, shadow, and brief occlusion false positives."
                    >
                      <span className="flex items-center gap-1 underline decoration-dotted underline-offset-2 group-hover:text-dam-blue transition-colors">Temporal Frames <Info size={8} /></span>
                      <span className="opacity-40 italic">1+</span>
                    </label>
                    <input
                      id={`node-${index}-temporal-smoothing`}
                      type="number"
                      min="1"
                      step="1"
                      value={node.params.temporal_smoothing_frames ?? 3}
                      onChange={e => onChange({ ...node, params: { ...node.params, temporal_smoothing_frames: Math.max(1, Number(e.target.value) || 1) } })}
                      className={`w-full ${inputCls} h-9 rounded-lg`}
                    />
                 </div>
        </div>
            </div>
          )
        }

        // Skip callbacks that have dedicated UI above to avoid double rendering
        if (node.callback === 'joint_position_limits') return null
        if (node.callback === 'joint_velocity_limit') return null
        if (node.callback === 'task_gripper_command_guard') return null

        return (
          <div key={node.callback} className="space-y-1.5 border-t border-dam-border/40 pt-1.5">
            <p className="text-dam-muted text-[10px] uppercase font-bold tracking-widest">{node.callback} Parameters</p>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              {Object.entries(meta.params)
                .filter(([pName]) => !(node.callback === 'workspace' && pName === 'bounds'))
                .map(([pName, pMeta]) => (
                <div key={pName} className="space-y-0.5">
                  <label
                    htmlFor={`node-${index}-param-${pName}`}
                    className="text-dam-muted text-[10px] cursor-help"
                    title={pMeta.description || undefined}
                  >
                    <span className={pMeta.description ? 'underline decoration-dotted underline-offset-2' : ''}>{pName}</span>
                  </label>
                  <input
                    id={`node-${index}-param-${pName}`}
                    type="text"
                    title={pMeta.description || undefined}
                    value={node.params?.[pName] ?? pMeta.default ?? ''}
                    onChange={e => {
                      const val = e.target.value
                      const nextParams = { ...(node.params || {}) }
                      // Basic type conversion
                      if (!Number.isNaN(Number(val)) && val !== '') nextParams[pName] = Number(val)
                      else if (val === 'true') nextParams[pName] = true
                      else if (val === 'false') nextParams[pName] = false
                      else nextParams[pName] = val
                      onChange({ ...node, params: nextParams })
                    }}
                    className={`w-full ${inputCls}`}
                  />
                </div>
              ))}
            </div>
          </div>
        )
      })()}
    </div>
  )
}

// ── Boundary card (collapsible) ───────────────────────────────────────────────

function BoundaryCard({
  boundary,
  isActive,
  onChange,
  onRemove,
  callbackCatalog = [],
  callbackGroups = [],
  fallbackCatalog = [],
  boundaryTemplates = [],
  existingBoundaryNames = new Set<string>(),
}: {
  boundary: BoundaryDef
  isActive?: boolean
  onChange: (b: BoundaryDef) => void
  onRemove: () => void
  callbackCatalog?: BoundaryCallbackDef[]
  callbackGroups?: BoundaryCallbackGroupDef[]
  fallbackCatalog?: FallbackDef[]
  boundaryTemplates?: BoundaryTemplateDef[]
  existingBoundaryNames?: Set<string>
}) {
  const [open, setOpen] = useState(true) // Default open to show fields
  const [activeIdx, setActiveIdx] = useState(0)

  const addNode = () => {
    const next = { ...boundary, nodes: [...boundary.nodes, makeNode()] }
    onChange(next)
  }
  const removeNode = (i: number) =>
    onChange({ ...boundary, nodes: boundary.nodes.filter((_, idx) => idx !== i) })
  const updateNode = (i: number, n: ConstraintNodeDef) => {
    if (
      n.callback === 'task_gripper_command_guard' &&
      boundary.layer === 'L2' &&
      boundary.type === 'single' &&
      boundary.nodes.length === 1 &&
      isAutoBoundaryName(boundary.name, boundary.layer)
    ) {
      const template = boundaryTemplates.find(t =>
        t.layer === boundary.layer &&
        t.boundary.nodes.some(node => node.callback === n.callback)
      )
      if (template) {
        onChange(materializeBoundaryTemplate(
          template,
          new Set([...existingBoundaryNames].filter(name => name !== boundary.name))
        ))
        setActiveIdx(0)
        return
      }
    }
    onChange({ ...boundary, nodes: boundary.nodes.map((nd, idx) => idx === i ? n : nd) })
  }

  const isList = boundary.type === 'list'
  const nodes = boundary.nodes ?? []
  const clampedActive = Math.min(activeIdx, Math.max(0, nodes.length - 1))

  return (
    <div className={`rounded-lg bg-dam-surface-2 border overflow-hidden transition-colors ${
      isActive ? 'border-dam-blue/40' : 'border-dam-border'
    }`}>
      {/* Header row */}
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          onClick={() => setOpen(v => !v)}
          className="text-dam-muted hover:text-dam-blue transition-colors shrink-0"
        >
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </button>
        <input
          value={boundary.name}
          onChange={e => onChange({ ...boundary, name: e.target.value })}
          placeholder="boundary_name"
          className={`flex-1 min-w-0 ${inputCls}`}
        />
        <select
          value={boundary.type}
          onChange={e => {
            onChange({ ...boundary, type: e.target.value as BoundaryDef['type'] })
            setActiveIdx(0)
          }}
          className={`w-20 shrink-0 ${inputCls}`}
        >
          <option value="single">single</option>
          <option value="list">list</option>
        </select>
        <span className="text-dam-muted text-[10px] shrink-0 font-mono italic opacity-60">{nodes.length} node{nodes.length !== 1 ? 's' : ''}</span>
        {isActive && (
          <span className="text-[9px] px-2 py-0.5 bg-dam-blue/10 border border-dam-blue/30 text-dam-blue rounded-full font-bold tracking-tight shrink-0">
            Active
          </span>
        )}
        <button onClick={onRemove} className="text-dam-muted hover:text-dam-red transition-colors shrink-0">
          <Trash2 size={13} />
        </button>
      </div>

      {/* Expanded nodes */}
      {open && (
        <div className="px-3 pb-3 space-y-2 border-t border-dam-border/60">

          {/* List container: active-node navigator */}
          {isList && nodes.length > 0 && (
            <div className="flex items-center gap-2 pt-2">
              <List size={11} className="text-dam-muted" />
              <span className="text-dam-muted text-[10px] flex-1">
                Sequence — node{' '}
                <span className="text-dam-blue font-mono">{clampedActive + 1}</span>
                {' / '}{nodes.length} active
              </span>
              <div className="flex gap-1">
                {nodes.map((n, i) => (
                  <button
                    key={n.node_id || `node-${i}`}
                    onClick={() => setActiveIdx(i)}
                    className={`w-2 h-2 rounded-full transition-all ${
                      i === clampedActive
                        ? 'bg-dam-blue'
                        : 'bg-dam-border hover:bg-dam-muted'
                    }`}
                    title={`Node ${i + 1}`}
                  />
                ))}
              </div>
              <button
                onClick={() => setActiveIdx(i => Math.max(0, i - 1))}
                disabled={clampedActive === 0}
                className="p-0.5 text-dam-muted hover:text-dam-blue disabled:opacity-30 transition-colors"
              >
                <ChevronLeft size={13} />
              </button>
              <button
                onClick={() => setActiveIdx(i => Math.min(nodes.length - 1, i + 1))}
                disabled={clampedActive >= nodes.length - 1}
                className="p-0.5 text-dam-muted hover:text-dam-blue disabled:opacity-30 transition-colors"
              >
                <ChevronRight size={13} />
              </button>
            </div>
          )}

          {!isList && (
            <p className="text-dam-muted text-[10px] uppercase tracking-wider pt-2">Constraint Nodes</p>
          )}

          {nodes.map((node, i) => (
            <NodeForm
              key={node.node_id || `node-${i}`}
              node={node}
              index={i}
              isActive={isList ? i === clampedActive : undefined}
              onChange={n => updateNode(i, n)}
              onRemove={() => removeNode(i)}
              callbackCatalog={callbackCatalog}
              callbackGroups={callbackGroups}
              fallbackCatalog={fallbackCatalog}
              allowNodeIdEdit={isList}
              boundaryName={boundary.name}
              boundaryLayer={boundary.layer}
              onBoundaryNameChange={nextName => onChange({ ...boundary, name: nextName })}
              existingBoundaryNames={existingBoundaryNames}
            />
          ))}
          <button
            onClick={addNode}
            className="flex items-center gap-1 text-[10px] text-dam-muted hover:text-dam-blue transition-colors"
          >
            <Plus size={10} /> Add node
          </button>
        </div>
      )}
    </div>
  )
}

// ── Boundary Picker Modal ──────────────────────────────────────────────────

function BoundaryPickerModal({
  isOpen,
  onClose,
  allBoundaries,
  selectedNames,
  onToggle,
}: {
  isOpen: boolean
  onClose: () => void
  allBoundaries: BoundaryDef[]
  selectedNames: string[]
  onToggle: (name: string) => void
}) {
  if (!isOpen) return null

  const grouped = allBoundaries.reduce((acc, b) => {
    const layer = b.layer || 'L1'
    if (!acc[layer]) acc[layer] = []
    acc[layer].push(b)
    return acc
  }, {} as { [key: string]: BoundaryDef[] })

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
      <button type="button" aria-label="Close" className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative bg-dam-surface border border-dam-border rounded-2xl w-full max-w-lg shadow-2xl flex flex-col max-h-[80vh]">
        <div className="flex items-center justify-between p-4 border-b border-dam-border">
          <div className="flex items-center gap-2">
            <Layers size={16} className="text-dam-blue" />
            <h3 className="text-sm font-bold text-dam-text uppercase tracking-widest">Select Boundaries</h3>
          </div>
          <button onClick={onClose} className="p-1 hover:bg-white/5 rounded-full transition-colors">
            <X size={16} className="text-dam-muted" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-5 thin-scrollbar">
          {Object.keys(grouped).sort((a, b) => a.localeCompare(b)).map(layer => (
            <div key={layer} className="space-y-1.5">
              <p className={`text-[10px] font-bold uppercase tracking-[0.1em] ${LAYER_COLORS[layer] || 'text-dam-muted'}`}>
                {layer} Layer
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {grouped[layer].map(b => {
                  const active = selectedNames.includes(b.name)
                  return (
                    <button
                      key={b.name}
                      onClick={() => onToggle(b.name)}
                      className={`flex items-center justify-between px-3 py-2 rounded-lg border text-left transition-all ${
                        active
                          ? 'bg-dam-blue-dim border-dam-blue text-dam-blue'
                          : 'bg-dam-surface-3 border-dam-border/60 text-dam-muted hover:border-dam-blue/30'
                      }`}
                    >
                      <span className="text-xs font-mono truncate mr-2">{b.name}</span>
                      <div className={`w-3 h-3 rounded-full border-2 flex items-center justify-center transition-colors ${active ? 'border-dam-blue bg-dam-blue' : 'border-dam-border'}`}>
                        {active && <Check size={8} className="text-white font-bold" />}
                      </div>
                    </button>
                  )
                })}
              </div>
            </div>
          ))}
          {allBoundaries.length === 0 && (
            <div className="text-center py-10">
              <p className="text-xs text-dam-muted italic">No boundaries defined yet. Add some in the Boundaries section.</p>
            </div>
          )}
        </div>

        <div className="p-4 border-t border-dam-border flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-1.5 bg-dam-blue text-white text-xs font-bold rounded hover:bg-dam-blue-bright transition-colors"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Task form ─────────────────────────────────────────────────────────────────

function TaskForm({
  task,
  boundaries,
  onChange,
  onRemove,
}: {
  task: TaskDef
  boundaries: BoundaryDef[]
  onChange: (t: TaskDef) => void
  onRemove: () => void
}) {
  const [isPickerOpen, setIsPickerOpen] = useState(false)

  const toggleBoundary = (name: string) => {
    const next = task.boundaries.includes(name)
      ? task.boundaries.filter(b => b !== name)
      : [...task.boundaries, name]
    onChange({ ...task, boundaries: next })
  }

  const selectedBoundaries = boundaries.filter(b => task.boundaries.includes(b.name))

  return (
    <div className="p-3 rounded-lg bg-dam-surface-2 border border-dam-border space-y-2">
      <div className="grid grid-cols-1 sm:grid-cols-[1fr_2fr] gap-3">
        <div className="space-y-1">
          <label htmlFor={`task-${task.id}-name`} className="text-dam-muted text-[10px] uppercase tracking-wider">Task ID / Name</label>
          <div className="flex items-center gap-2">
            <input
              id={`task-${task.id}-name`}
              value={task.name}
              onChange={e => onChange({ ...task, name: e.target.value })}
              placeholder="task_name"
              className={`flex-1 ${inputCls}`}
            />
            <button onClick={onRemove} className="text-dam-muted hover:text-dam-red transition-colors shrink-0">
              <Trash2 size={13} />
            </button>
          </div>
        </div>
        <div className="space-y-1">
          <label htmlFor={`task-${task.id}-desc`} className="text-dam-muted text-[10px] uppercase tracking-wider">Description</label>
          <input
            id={`task-${task.id}-desc`}
            value={task.description}
            onChange={e => onChange({ ...task, description: e.target.value })}
            placeholder="e.g. tabletop assembly"
            className={`w-full ${inputCls}`}
          />
        </div>
      </div>
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <p className="text-dam-muted text-[10px]">Active boundaries</p>
          <button
            onClick={() => setIsPickerOpen(true)}
            className="text-[10px] text-dam-blue hover:underline flex items-center gap-1"
          >
            <Plus size={10} /> Add Boundaries
          </button>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {selectedBoundaries.length === 0 && (
            <span className="text-dam-muted text-[10px] italic">No boundaries selected</span>
          )}
          {selectedBoundaries.map(b => (
            <div
              key={b.name}
              className="px-2 py-0.5 rounded text-[10px] border border-dam-blue bg-dam-blue-dim text-dam-blue flex items-center gap-1.5"
            >
              <span className={`text-[8px] px-1 rounded border ${LAYER_COLORS[b.layer] ?? ''}`}>{b.layer}</span>
              <span className="font-mono">{b.name}</span>
              <button
                onClick={() => toggleBoundary(b.name)}
                className="text-dam-blue/60 hover:text-dam-blue transition-colors"
                title="Remove from task"
              >
                <X size={9} />
              </button>
            </div>
          ))}
        </div>
      </div>

      <BoundaryPickerModal
        isOpen={isPickerOpen}
        onClose={() => setIsPickerOpen(false)}
        allBoundaries={boundaries}
        selectedNames={task.boundaries}
        onToggle={toggleBoundary}
      />
    </div>
  )
}


// ── Main page ─────────────────────────────────────────────────────────────────

function migrateNode(node: ConstraintNodeDef): ConstraintNodeDef {
  const next = { ...node, params: { ...(node.params || {}) } }
  // Clean up stray fields that shouldn't be in params
  delete next.params.layer
  delete next.params.type
  // Migrate .dam_data/ood_models -> data/ood_models
  if (next.params.ood_model_path?.includes('.dam_data/ood_models')) {
    next.params.ood_model_path = next.params.ood_model_path.replaceAll('.dam_data/ood_models', 'data/ood_models')
  }
  if (next.params.bank_path?.includes('.dam_data/ood_models')) {
    next.params.bank_path = next.params.bank_path.replaceAll('.dam_data/ood_models', 'data/ood_models')
  }
  // Migrate nested joint_position_limits -> flat upper/lower
  if (next.params.joint_position_limits && typeof next.params.joint_position_limits === 'object' && !Array.isArray(next.params.joint_position_limits)) {
    const jl = next.params.joint_position_limits as any
    if (jl.upper) next.params.upper = jl.upper
    if (jl.lower) next.params.lower = jl.lower
    delete next.params.joint_position_limits
  }
  return normalizeGripperNode(next)
}

function migrateConfig(parsed: any) {
  if (parsed.boundaries) {
    parsed.boundaries = parsed.boundaries
      .filter((b: any) => typeof b.name === 'string' && b.name.trim())
      .map((b: any) => ({
        ...b,
        name: b.name.trim(),
        nodes: b.nodes?.map(migrateNode) || []
      }))
  }
  if (parsed.tasks) {
    const validNames = parsed.boundaries
      ? new Set(parsed.boundaries.map((b: any) => b.name))
      : null
    parsed.tasks = parsed.tasks.map((t: any) => ({
      ...t,
      boundaries: validNames
        ? (t.boundaries || []).filter((name: string) => validNames.has(name))
        : (t.boundaries || [])
    }))
  }
  return parsed
}

export default function GuardPage() {
  const [callbackCatalog, setCallbackCatalog] = useState<BoundaryCallbackDef[]>([])
  const [callbackGroups, setCallbackGroups] = useState<BoundaryCallbackGroupDef[]>([])
  const [boundaryTemplates, setBoundaryTemplates] = useState<BoundaryTemplateDef[]>([])
  const [guardCatalog, setGuardCatalog] = useState<GuardDef[]>([])
  const [fallbackCatalog, setFallbackCatalog] = useState<FallbackDef[]>([])

  const [tasks, setTasks] = useState<TaskDef[]>([])

  const [boundaries, setBoundaries] = useState<BoundaryDef[]>([])

  const [guardsEnabled, setGuardsEnabled] = useState<Record<string, boolean>>({})


  // Expand/collapse for Boundaries section (per layer)
  const [expandedBoundaryLayers, setExpandedBoundaryLayers] = useState<Record<string, boolean>>({
    L0: false, L1: false, L2: false, L3: false,
  })

  const [saved, setSaved] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const [restartError, setRestartError] = useState<string | null>(null)
  const [restartOk, setRestartOk] = useState(false)
  const importRef = useRef<HTMLInputElement>(null)

  // ── Stackfile library (shared with the Config page via a hook) ─────────
  const lib = useStackfileLibrary()

  const applyStackfileYaml = useCallback((content: string) => {
    if (!content) return
    const parsed = migrateConfig(parseConfigFromYaml(content)) as Partial<DamConfig>
    if (parsed.tasks && parsed.tasks.length > 0) setTasks(parsed.tasks)
    if (parsed.boundaries && parsed.boundaries.length > 0) setBoundaries(parsed.boundaries)
    if (parsed.guardsEnabled) setGuardsEnabled(parsed.guardsEnabled)
    // Keep the localStorage working copy in sync so the YAML preview and
    // autosave (which merge from it) reflect the loaded stackfile.
    try {
      const rawCfg = localStorage.getItem('dam_config_v1')
      const base: DamConfig = rawCfg ? { ...defaultConfig(), ...JSON.parse(rawCfg) } : defaultConfig()
      localStorage.setItem('dam_config_v1', JSON.stringify({ ...base, ...parsed }))
    } catch { /* ignore */ }
  }, [])

  // Load the active stackfile (live or a library config) once after mount.
  useEffect(() => {
    lib.bootstrap(applyStackfileYaml)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Boundaries referenced by ANY task = "active"
  const activeBoundaryNames = new Set(tasks.flatMap(t => t.boundaries))

  // Hydrate state from localStorage on mount to avoid SSR mismatch
  useEffect(() => {
    try {
      const raw = localStorage.getItem('dam_config_v1')
      if (raw) {
        const migrated = migrateConfig(JSON.parse(raw))
        if (migrated.tasks?.length > 0) setTasks(migrated.tasks)
        else setTasks([{ id: crypto.randomUUID(), name: 'default', description: '', boundaries: ['workspace'] }])

        if (migrated.boundaries?.length > 0) setBoundaries(migrated.boundaries)
        else setBoundaries([
          {
            name: 'workspace',
            layer: 'L1',
            type: 'single',
            nodes: [{
              node_id: 'default',
              params: {
                bounds: [[-0.4, 0.4], [-0.4, 0.4], [0.02, 0.6]],
              },
              callback: 'workspace',
              fallback: 'emergency_stop',
              timeout_sec: 1,
            }],
          },
        ])

        if (migrated.guardsEnabled) setGuardsEnabled(migrated.guardsEnabled)
      } else {
        // Init defaults if nothing in localStorage
        setTasks([{ id: crypto.randomUUID(), name: 'default', description: '', boundaries: ['workspace'] }])
        setBoundaries([
          {
            name: 'workspace',
            layer: 'L1',
            type: 'single',
            nodes: [{
              node_id: 'default',
              params: {
                bounds: [[-0.4, 0.4], [-0.4, 0.4], [0.02, 0.6]],
              },
              callback: 'workspace',
              fallback: 'emergency_stop',
              timeout_sec: 1,
            }],
          },
        ])
      }
    } catch {
      setTasks([{ id: crypto.randomUUID(), name: 'default', description: '', boundaries: ['workspace'] }])
    }
  }, [])


  const syncStatus = useCallback(async () => {
    // Rust status check removed as per user request
  }, [])

  const syncCallbacks = useCallback(async () => {
    try {
      const [cdata, gdata, groupData, fdata, templateData] = await Promise.all([
        api.getCallbacks(),
        api.getGuardCatalog(),
        api.getCallbackCatalog(true),
        api.getFallbacks(),
        api.getBoundaryTemplates(),
      ])

      if (cdata.callbacks) setCallbackCatalog(cdata.callbacks)
      if (gdata.guards) {
        setGuardCatalog(gdata.guards.map(g => ({
          id: g.kind,
          name: `${g.kind.charAt(0).toUpperCase() + g.kind.slice(1)} Guard`,
          layer: g.layer,
          description: g.description
        })))
      }
      if (groupData.groups) setCallbackGroups(groupData.groups)
      if (fdata.fallbacks) setFallbackCatalog(fdata.fallbacks)
      if (templateData.templates) setBoundaryTemplates(templateData.templates)

      if (cdata.callbacks) {
        // Only perform mandatory cleanup and metadata updates, NO AUTO-ADDING
        setBoundaries(prev => {
          const next = [...prev]
          let globalChanged = false

          next.forEach(b => {
             const node = b.nodes[0]
             if (node) {
               // Fix null timeouts
               if (node.timeout_sec === null) {
                 node.timeout_sec = 1
                 globalChanged = true
               }
               // Sync specialized callback logic if missing
               const cb = cdata.callbacks.find(c => c.name === node.callback)
               if (cb && !node.params?.[cb.name] && (cb.name === 'joint_position_limits' || cb.name === 'workspace')) {
                 // Skip auto-injecting nested objects here to avoid duplicates
               }
             }
          })
          return globalChanged ? next : prev
        })
      }
    } catch {}
  }, [])

  useEffect(() => {
    syncStatus()
    syncCallbacks()
    // React to backend config/status changes pushed via the telemetry WS
    // instead of polling on a fixed interval.
    const handleSystemUpdate = () => {
      syncStatus()
      syncCallbacks()
    }
    globalThis.addEventListener('dam-system-update', handleSystemUpdate)
    return () => globalThis.removeEventListener('dam-system-update', handleSystemUpdate)
  }, [syncStatus, syncCallbacks])

  // Auto-save to localStorage + disk (debounced)
  useEffect(() => {
    const t = setTimeout(async () => {
      try {
        const rawCfg = globalThis.window !== undefined ? localStorage.getItem('dam_config_v1') : null
        const cfg: DamConfig = rawCfg ? { ...defaultConfig(), ...JSON.parse(rawCfg) } : defaultConfig()
        cfg.tasks = tasks
        cfg.boundaries = boundaries
        cfg.guardsEnabled = guardsEnabled
        cfg.guardsEnabled = guardsEnabled

        const yaml = generateYaml(cfg)
        localStorage.setItem('dam_config_v1', JSON.stringify(cfg))
        localStorage.setItem('dam_yaml_v1', yaml)

        // Persist to the active stackfile target (live or a library config).
        await lib.save(yaml)

        setSaved(true)
        setTimeout(() => setSaved(false), 1200)
      } catch {}
    }, 500)
    return () => clearTimeout(t)
  }, [tasks, boundaries, guardsEnabled, lib])

  const addTask = () => setTasks(prev => [...prev, makeTask()])
  const removeTask = (id: string) => setTasks(prev => prev.filter(t => t.id !== id))
  const updateTask = (t: TaskDef) => setTasks(prev => prev.map(x => x.id === t.id ? t : x))

  const addBoundary = (layer = 'L1') => setBoundaries(prev => [...prev, makeBoundary(layer)])
  const removeBoundary = (name: string) => setBoundaries(prev => prev.filter(b => b.name !== name))
  const updateBoundary = (b: BoundaryDef, origName: string) =>
    setBoundaries(prev => prev.map(x => x.name === origName ? b : x))

  const toggleGuard = (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setGuardsEnabled(prev => ({ ...prev, [id]: prev[id] === false }))
  }


  const handleExport = () => {
    const rawCfg = globalThis.window !== undefined ? localStorage.getItem('dam_config_v1') : null
    const cfg: DamConfig = rawCfg ? { ...defaultConfig(), ...JSON.parse(rawCfg) } : defaultConfig()
    cfg.tasks = tasks
    cfg.boundaries = boundaries
    cfg.guardsEnabled = guardsEnabled
    const blob = new Blob([JSON.stringify(cfg, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'dam_config.json'
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleImport = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ''
    file.text().then(text => {
      try {
        const parsed = JSON.parse(text) as DamConfig
        if (parsed.tasks) setTasks(parsed.tasks)
        if (parsed.boundaries) setBoundaries(parsed.boundaries)
        if (parsed.guardsEnabled) setGuardsEnabled(parsed.guardsEnabled)
      } catch {}
    })
  }

  const yamlPreview = (() => {
    try {
      const rawCfg = globalThis.window !== undefined ? localStorage.getItem('dam_config_v1') : null
      const cfg: DamConfig = rawCfg ? { ...defaultConfig(), ...JSON.parse(rawCfg) } : defaultConfig()
      cfg.tasks = tasks
      cfg.boundaries = boundaries
      cfg.guardsEnabled = guardsEnabled
      return generateYaml(cfg)
    } catch {
      return ''
    }
  })()

  const handleApplyRestart = async () => {
    setRestarting(true)
    setRestartError(null)
    setRestartOk(false)
    try {
      const rawCfg = globalThis.window !== undefined ? localStorage.getItem('dam_config_v1') : null
      const cfg: DamConfig = rawCfg ? { ...defaultConfig(), ...JSON.parse(rawCfg) } : defaultConfig()
      cfg.tasks = tasks
      cfg.boundaries = boundaries
      cfg.guardsEnabled = guardsEnabled
      localStorage.setItem('dam_config_v1', JSON.stringify(cfg))

      const fullYaml = generateYaml(cfg)
      await api.restart(cfg.adapter, fullYaml)
      setRestartOk(true)
      setTimeout(() => setRestartOk(false), 3000)
    } catch (e) {
      setRestartError(e instanceof Error ? e.message : String(e))
    } finally {
      setRestarting(false)
    }
  }

  return (
    <ActionShell
      title="Guard"
      description="Safety pipeline & boundaries"
      restarting={restarting}
      restartOk={restartOk}
      restartError={restartError}
      saved={saved}
      yaml={yamlPreview}
      onApply={handleApplyRestart}
      onImport={() => importRef.current?.click()}
      onExport={handleExport}
    >
      <input ref={importRef} type="file" accept=".json" onChange={handleImport} className="hidden" />

      <StackfileLibraryBar lib={lib} getYaml={() => yamlPreview} onLoaded={applyStackfileYaml} />

      {/* Summary / Status Bar */}
      {tasks.length > 0 && (
        <div className="flex gap-4 p-3 rounded-lg bg-dam-surface-2 border border-dam-border/60">
          <div className="flex-1 flex items-center gap-3">
            <LayoutDashboard size={14} className="text-dam-blue" />
            <div className="flex flex-col">
              <span className="text-[10px] text-dam-muted uppercase font-bold leading-none">Tasks</span>
              <span className="text-xs font-mono font-bold text-dam-text">{tasks.length} defined</span>
            </div>
          </div>
          <div className="w-px h-8 bg-dam-border" />
          <div className="flex-1 flex items-center gap-3">
            <Layout size={14} className="text-dam-blue" />
            <div className="flex flex-col">
              <span className="text-[10px] text-dam-muted uppercase font-bold leading-none">Active Boundaries</span>
              <span className="text-xs font-mono font-bold text-dam-text">{activeBoundaryNames.size} across all tasks</span>
            </div>
          </div>
          <div className="w-px h-8 bg-dam-border" />
          <div className="flex-1 flex items-center gap-3">
            <ShieldCheck size={14} className="text-dam-green" />
            <div className="flex flex-col">
              <span className="text-[10px] text-dam-muted uppercase font-bold leading-none">Guards Enabled</span>
              <span className="text-xs font-mono font-bold text-dam-text">
                {guardCatalog.filter(g => guardsEnabled[g.id] !== false).length} / {guardCatalog.length}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Boundaries & Guard Configuration (Merged) */}
      <div className="glass-card p-6 space-y-4">
        <h2 className="text-dam-muted text-xs uppercase tracking-widest font-semibold relative z-10">Boundaries</h2>
        <div className="space-y-2 relative z-10">
          {guardCatalog.map(g => {
            const layerBoundaries = boundaries.filter(b => b.layer === g.layer)
            const layerCallbackCatalog = callbackCatalog.filter(cb => cb.layer === g.layer)
            const layerBoundaryTemplates = boundaryTemplates.filter(t => t.layer === g.layer)
            const layerCallbackGroups = callbackGroups
              .filter(group => group.layer === g.layer)
              .map(group => ({
                ...group,
                callbacks: group.callbacks.filter(cb => cb.layer === g.layer),
              }))
            const isExpanded = expandedBoundaryLayers[g.layer]
            const activeInLayer = layerBoundaries.filter(b => activeBoundaryNames.has(b.name)).length
            const isEnabled = guardsEnabled[g.id] !== false
            const existingBoundaryNames = new Set(boundaries.map(b => b.name).filter(Boolean))

            return (
              <div key={g.layer} className={`border border-dam-border/40 rounded-xl overflow-hidden ${
                isEnabled ? '' : 'opacity-60 grayscale'
              }`}>
                {/* Layer header */}
                <div className="flex items-center gap-3 px-3 py-2.5 hover:bg-dam-surface-2/50 transition-colors">
                  <button
                    type="button"
                    className="flex items-center gap-3 flex-1 min-w-0 text-left"
                    onClick={() => setExpandedBoundaryLayers(prev => ({ ...prev, [g.layer]: !prev[g.layer] }))}
                  >
                    <span className="text-dam-muted p-0.5 hover:text-dam-text shrink-0">
                      {isExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                    </span>
                    <span className={`text-[9px] px-1.5 py-0.5 rounded border font-mono shrink-0 ${LAYER_COLORS[g.layer] ?? ''}`}>
                      {g.layer}
                    </span>
                    <div className="flex-1 min-w-0 flex items-center gap-3">
                      <span className={`text-sm font-semibold transition-colors ${isEnabled ? 'text-dam-text' : 'text-dam-muted'}`}>
                        {g.name}
                      </span>
                    {activeInLayer > 0 && isEnabled && (
                      <div className="flex items-center gap-1 px-1.5 py-0.5 bg-dam-blue/10 border border-dam-blue/20 rounded-md shrink-0">
                        <div className="w-1 h-1 rounded-full bg-dam-blue animate-pulse" />
                        <span className="text-[10px] font-bold text-dam-blue uppercase">
                          {activeInLayer} Active
                        </span>
                      </div>
                    )}
                    </div>
                  </button>

                  <button
                    type="button"
                    onClick={e => toggleGuard(g.id, e)}
                    className="shrink-0 transition-colors"
                  >
                    {isEnabled
                      ? <ToggleRight size={22} className="text-dam-blue" />
                      : <ToggleLeft size={22} className="text-dam-muted" />
                    }
                  </button>

                  <button
                    type="button"
                    onClick={() => addBoundary(g.layer)}
                    className="flex items-center gap-1 text-[10px] text-dam-muted hover:text-dam-blue border border-dam-border px-2 py-1 rounded bg-dam-surface-3 transition-colors shrink-0"
                  >
                    <Plus size={9} /> Add
                  </button>
                </div>

                {isExpanded && (
                  <div className="p-4 space-y-4 bg-dam-surface-2/30 border-t border-dam-border/10">
                    <div className="space-y-1">
                      <p className="text-dam-muted text-[11px] leading-relaxed max-w-2xl">
                        {g.description}
                      </p>
                    </div>

                    {/* Layer description */}

                    {/* Layer boundaries */}
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                         <h4 className="text-[10px] font-bold uppercase tracking-widest text-dam-muted opacity-50">Configured Boundaries</h4>
                         <button
                            onClick={e => { e.stopPropagation(); addBoundary(g.layer) }}
                            className="flex items-center gap-1 text-[9px] text-dam-blue border border-dam-blue/20 px-2 py-0.5 rounded-full bg-dam-blue/5 hover:bg-dam-blue/10 transition-colors shrink-0"
                          >
                            <Plus size={8} /> New Constraint
                          </button>
                      </div>

                      {layerBoundaries.length === 0 ? (
                        <div className="text-center py-6 border-2 border-dashed border-dam-border/20 rounded-xl">
                          <p className="text-dam-muted text-[10px] italic">
                            No {g.layer} active constraints.
                          </p>
                        </div>
                      ) : (
                        layerBoundaries.map((b, i) => (
                          <BoundaryCard
                            key={`${b.name}-${i}`}
                            boundary={b}
                            isActive={activeBoundaryNames.has(b.name)}
                            onChange={nb => updateBoundary(nb, b.name)}
                            onRemove={() => removeBoundary(b.name)}
                            callbackCatalog={layerCallbackCatalog}
                            callbackGroups={layerCallbackGroups}
                            fallbackCatalog={fallbackCatalog}
                            boundaryTemplates={layerBoundaryTemplates}
                            existingBoundaryNames={existingBoundaryNames}
                          />
                        ))
                      )}
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {/* ── Tasks ─────────────────────────────────────────────────────────────── */}
      <div className="glass-card p-6 space-y-4">
        <div className="flex items-center justify-between relative z-10">
          <h2 className="text-dam-muted text-xs uppercase tracking-widest font-semibold">Tasks</h2>
          <button
            onClick={addTask}
            className="flex items-center gap-1 text-xs text-dam-muted hover:text-dam-blue transition-colors"
          >
            <Plus size={12} /> New Task
          </button>
        </div>
        {tasks.length === 0 && (
          <p className="text-dam-muted text-xs italic">No tasks. Click &ldquo;New Task&rdquo; to create one.</p>
        )}
        <div className="space-y-2 relative z-10">
          {tasks.map(t => (
            <TaskForm
              key={t.id}
              task={t}
              boundaries={boundaries}
              onChange={updateTask}
              onRemove={() => removeTask(t.id)}
            />
          ))}
        </div>
      </div>

    </ActionShell>
  )
}
