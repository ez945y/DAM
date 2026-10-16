'use client'

import { useRef } from 'react'
import { safetyButton, safetyInput, type SafetyNode } from '@/lib/safety'

type Vec3 = [number, number, number]
type Bounds = [[number, number], [number, number], [number, number]]
const project = ([x, y, z]: Vec3): [number, number] => [220 + (x - y) * 140, 225 + (x + y) * 70 - z * 140]
const points = (vertices: Vec3[]) => vertices.map(v => project(v).join(',')).join(' ')
function validRows(value: unknown, rows: number, cols: number): value is number[][] {
  return Array.isArray(value) && value.length === rows && value.every(row => Array.isArray(row) && row.length === cols && row.every(n => typeof n === 'number' && Number.isFinite(n)))
}

/** Coordinates are always the configured FK solver's native coordinates. */
export function RegionEditor({ node, onChange }: { node: SafetyNode; onChange: (node: SafetyNode) => void }) {
  const drag = useRef<{ x: number; y: number; node: SafetyNode } | null>(null)
  const isBox = node.callback === 'workspace'
  const bounds = validRows(node.params.bounds, 3, 2) ? node.params.bounds as Bounds : null
  const spheres = Array.isArray(node.params.spheres) && node.params.spheres.length > 0 && validRows(node.params.spheres, node.params.spheres.length, 4) ? node.params.spheres : null
  if (isBox ? !bounds : !spheres) return <p className="text-dam-orange text-xs">Region data is incomplete. Edit the boundary JSON to supply {isBox ? 'three min/max bounds' : 'spheres [x, y, z, radius]'}.</p>
  const center: Vec3 = isBox ? bounds!.map(pair => (pair[0] + pair[1]) / 2) as Vec3 : spheres![0].slice(0, 3) as Vec3
  const updateParam = (key: string, value: unknown) => onChange({ ...node, params: { ...node.params, [key]: value } })
  const invalid = isBox ? bounds!.some(([min, max]) => min >= max) : spheres!.some(s => s[3] <= 0)
  const b = bounds
  const vertices: Vec3[] = b ? [
    [b[0][0], b[1][0], b[2][0]], [b[0][1], b[1][0], b[2][0]],
    [b[0][1], b[1][1], b[2][0]], [b[0][0], b[1][1], b[2][0]],
    [b[0][0], b[1][0], b[2][1]], [b[0][1], b[1][0], b[2][1]],
    [b[0][1], b[1][1], b[2][1]], [b[0][0], b[1][1], b[2][1]],
  ] : []
  return <div className="space-y-3 rounded border border-dam-border p-3">
    <p className="text-xs text-dam-muted">{isBox ? 'Workspace box' : 'Keep-out spheres'} · metres · drag the shape along X/Y; edit Z below.</p>
    <svg viewBox="0 0 440 320" role="img" aria-label="Interactive 3D safety region" className="w-full max-h-72 bg-dam-bg rounded touch-none"
      onPointerMove={event => {
        const start = drag.current
        if (!start) return
        const rect = event.currentTarget.getBoundingClientRect()
        const scale = 1 / Math.max(Math.min(rect.width / 440, rect.height / 320), 0.001)
        const dx = (event.clientX - start.x) * scale
        const dy = (event.clientY - start.y) * scale
        const shiftX = dx / 280 + dy / 140
        const shiftY = -dx / 280 + dy / 140
        if (isBox) {
          const original = start.node.params.bounds as Bounds
          updateParam('bounds', original.map((pair, axis) => pair.map(v => Number((v + (axis === 0 ? shiftX : axis === 1 ? shiftY : 0)).toFixed(4)))))
        } else {
          const original = start.node.params.spheres as number[][]
          updateParam('spheres', original.map(s => [Number((s[0] + shiftX).toFixed(4)), Number((s[1] + shiftY).toFixed(4)), s[2], s[3]]))
        }
      }}
      onPointerUp={() => { drag.current = null }} onPointerCancel={() => { drag.current = null }}>
      {[0, 1, 2].map(axis => {
        const end: Vec3 = [0, 0, 0]; end[axis] = 1
        const [x, y] = project(end)
        return <g key={axis}><line x1="220" y1="225" x2={x} y2={y} stroke="#64748b" strokeDasharray="3 4" /><text x={x + 6} y={y} fill="#94a3b8" fontSize="12">{'XYZ'[axis]}</text></g>
      })}
      <g className="cursor-move" onPointerDown={event => {
        event.preventDefault()
        drag.current = { x: event.clientX, y: event.clientY, node }
        event.currentTarget.ownerSVGElement?.setPointerCapture(event.pointerId)
      }}>
        {isBox ? <>
          {[[0, 1, 2, 3], [0, 1, 5, 4], [1, 2, 6, 5], [4, 5, 6, 7], [0, 3, 7, 4]].map((face, i) => <polygon key={i} points={points(face.map(n => vertices[n]))} fill="#38bdf815" stroke="#38bdf8" strokeWidth="1.5" />)}
        </> : spheres!.map((sphere, i) => {
          const [x, y] = project(sphere.slice(0, 3) as Vec3)
          return <g key={i}><circle cx={x} cy={y} r={Math.max(1, sphere[3] * 140)} fill="#f9731625" stroke="#fb923c" /><ellipse cx={x} cy={y} rx={Math.max(1, sphere[3] * 140)} ry={Math.max(1, sphere[3] * 50)} fill="none" stroke="#fb923c" strokeDasharray="3 3" /></g>
        })}
        <circle cx={project(center)[0]} cy={project(center)[1]} r="5" fill="#e2e8f0" />
      </g>
    </svg>
    {isBox ? <div className="grid grid-cols-3 gap-2">{bounds!.map((pair, axis) => <div key={axis} className="space-y-1">
      {pair.map((value, side) => <label key={side} className="block text-xs text-dam-muted">{'XYZ'[axis]} {side ? 'max' : 'min'} (m)<input className={safetyInput} type="number" step="0.01" value={value} onChange={event => {
        const number = event.currentTarget.valueAsNumber
        if (!Number.isFinite(number)) return
        const next = bounds!.map(p => [...p]); next[axis][side] = number; updateParam('bounds', next)
      }} /></label>)}
    </div>)}</div> : <div className="space-y-2">{spheres!.map((sphere, index) => <div key={index} className="grid grid-cols-4 gap-2">{sphere.map((value, axis) => <label key={axis} className="text-xs text-dam-muted">Sphere {index + 1} {['X', 'Y', 'Z', 'radius'][axis]} (m)<input type="number" step="0.01" min={axis === 3 ? 0.001 : undefined} value={value} className={safetyInput} onChange={event => {
      const number = event.currentTarget.valueAsNumber
      if (!Number.isFinite(number)) return
      const next = spheres!.map(s => [...s]); next[index][axis] = number; updateParam('spheres', next)
    }} /></label>)}<button type="button" className={`${safetyButton} col-span-4 justify-self-end`} aria-label={`Remove sphere ${index + 1}`} disabled={spheres!.length === 1} title={spheres!.length === 1 ? 'Keep at least one sphere, or remove the entire rule explicitly.' : undefined} onClick={() => updateParam('spheres', spheres!.filter((_, i) => i !== index))}>Remove sphere</button></div>)}
      <button type="button" className={safetyButton} onClick={() => updateParam('spheres', [...spheres!, [0, 0, 0.2, 0.1]])}>Add sphere</button>
      {spheres!.length === 1 && <p className="text-xs text-dam-muted">Keep at least one sphere, or remove the entire rule explicitly.</p>}
    </div>}
    {invalid && <p role="alert" className="text-dam-red text-xs">Each minimum must be below its maximum; sphere radii must be positive. Fix these values before compiling.</p>}
  </div>
}
