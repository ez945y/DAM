'use client'
import { useState, useEffect } from 'react'
import { api } from '@/lib/api'
import type { BoundaryConfig } from '@/lib/types'
import { Plus, Pencil, Trash2 } from 'lucide-react'

function BoundaryModal({
  initial,
  onSave,
  onClose,
}: {
  initial?: BoundaryConfig
  onSave: (c: BoundaryConfig) => Promise<void>
  onClose: () => void
}) {
  const [name, setName] = useState(initial?.name ?? '')
  const [type, setType] = useState<'single' | 'list' | 'graph'>(initial?.type ?? 'single')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSave = async () => {
    if (!name.trim()) { setError('Name is required'); return }
    setSaving(true)
    try {
      await onSave({ name: name.trim(), type, nodes: initial?.nodes ?? [] })
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50" role="dialog" aria-modal="true">
      <div className="bg-dam-surface border border-dam-border rounded-xl p-6 w-full max-w-md space-y-4">
        <h3 className="text-dam-text font-bold">{initial ? 'Edit Boundary' : 'New Boundary'}</h3>
        <div className="space-y-3">
          <div>
            <label htmlFor="boundary-name" className="text-dam-muted text-xs uppercase tracking-wider block mb-1">Name</label>
            <input
              id="boundary-name"
              value={name}
              onChange={e => setName(e.target.value)}
              disabled={!!initial}
              placeholder="e.g. workspace"
              className="w-full bg-dam-surface-2 border border-dam-border rounded px-3 py-1.5 text-dam-text text-sm disabled:opacity-50"
            />
          </div>
          <div>
            <label htmlFor="boundary-type" className="text-dam-muted text-xs uppercase tracking-wider block mb-1">Type</label>
            <select
              id="boundary-type"
              value={type}
              onChange={e => setType(e.target.value as 'single' | 'list' | 'graph')}
              className="w-full bg-dam-surface-2 border border-dam-border rounded px-3 py-1.5 text-dam-text text-sm"
            >
              <option value="single">single</option>
              <option value="list">list</option>
              <option value="graph">graph</option>
            </select>
          </div>
        </div>
        {error && <p className="text-dam-red text-xs">{error}</p>}
        <div className="flex gap-2 justify-end">
          <button onClick={onClose} className="px-3 py-1.5 text-xs text-dam-muted border border-dam-border rounded hover:text-dam-text transition-colors">
            Cancel
          </button>
          <button
            onClick={() => void handleSave()}
            disabled={saving}
            className="px-4 py-1.5 text-xs font-bold bg-dam-blue text-white rounded hover:bg-dam-blue-bright disabled:opacity-50 transition-colors"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}

export function BoundaryList() {
  const [boundaries, setBoundaries] = useState<BoundaryConfig[]>([])
  const [loading, setLoading] = useState(false)
  const [showModal, setShowModal] = useState(false)
  const [editing, setEditing] = useState<BoundaryConfig | undefined>()

  const load = async () => {
    setLoading(true)
    try {
      const res = await api.listBoundaries()
      setBoundaries(res.boundaries)
    } catch { /* ignore */ } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])

  const handleCreate = async (c: BoundaryConfig) => {
    await api.createBoundary(c)
    await load()
  }

  const handleUpdate = async (c: BoundaryConfig) => {
    await api.updateBoundary(c.name, c)
    await load()
  }

  const handleDelete = async (name: string) => {
    if (!confirm(`Delete boundary "${name}"?`)) return
    try {
      await api.deleteBoundary(name)
      await load()
    } catch { /* ignore */ }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-dam-muted text-xs">{boundaries.length} boundary config{boundaries.length !== 1 ? 's' : ''}</p>
        <button
          onClick={() => { setEditing(undefined); setShowModal(true) }}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold bg-dam-blue text-white rounded hover:bg-dam-blue-bright transition-colors"
        >
          <Plus size={12} /> New Boundary
        </button>
      </div>

      {loading ? (
        <div className="text-dam-muted text-sm text-center py-8">Loading…</div>
      ) : boundaries.length === 0 ? (
        <div className="bg-dam-surface-2 border border-dashed border-dam-border rounded-xl p-8 text-center">
          <p className="text-dam-muted text-sm">No boundary configs yet.</p>
          <p className="text-dam-muted text-xs mt-1">Click &quot;New Boundary&quot; to create one.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {boundaries.map(b => (
            <div key={b.name} className="flex items-center gap-3 bg-dam-surface-2 border border-dam-border rounded-lg px-4 py-3 hover:border-dam-blue/50 transition-colors">
              <div className="flex-1 min-w-0">
                <p className="text-dam-text font-mono font-semibold text-sm">{b.name}</p>
                <p className="text-dam-muted text-xs">{b.type} · {b.nodes?.length ?? 0} node{(b.nodes?.length ?? 0) !== 1 ? 's' : ''}</p>
              </div>
              <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-dam-blue-dim text-dam-blue border border-blue-800 uppercase">
                {b.type}
              </span>
              <button
                onClick={() => { setEditing(b); setShowModal(true) }}
                className="p-1.5 text-dam-muted hover:text-dam-blue transition-colors rounded"
                title="Edit"
              >
                <Pencil size={13} />
              </button>
              <button
                onClick={() => void handleDelete(b.name)}
                className="p-1.5 text-dam-muted hover:text-dam-red transition-colors rounded"
                title="Delete"
              >
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </div>
      )}

      {showModal && (
        <BoundaryModal
          initial={editing}
          onSave={editing ? handleUpdate : handleCreate}
          onClose={() => setShowModal(false)}
        />
      )}
    </div>
  )
}
