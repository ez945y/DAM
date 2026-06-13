'use client'
import { useCallback, useEffect, useState } from 'react'
import { Plus, Pencil, Trash2, X, Check, Upload } from 'lucide-react'
import { listPresets, upsertPreset, deletePreset, type PresetEntry } from '@/lib/api'

const inputCls =
  'bg-dam-surface-2 border border-dam-border rounded px-2 py-1.5 text-xs font-mono text-dam-text focus:outline-none focus:border-dam-blue/60 transition-colors'

function emptyDraft(): PresetEntry {
  return { name: '', joint_names: [], degrees_mode: true, assets: {}, solvers: {} }
}

async function uploadAsset(file: File, target: string): Promise<string> {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('target', target)
  const res = await fetch('/api/system/upload-asset', { method: 'POST', body: fd })
  const body = await res.json() as { ok: boolean; path?: string; error?: string }
  if (!body.ok || !body.path) throw new Error(body.error ?? 'Upload failed')
  return body.path
}

export function PresetManager({
  open,
  onClose,
  onChanged,
}: {
  open: boolean
  onClose: () => void
  onChanged?: () => void
}) {
  const [presets, setPresets] = useState<PresetEntry[]>([])
  const [editing, setEditing] = useState<PresetEntry | null>(null)
  const [editingOriginalName, setEditingOriginalName] = useState<string | null>(null)
  const [jointsCsv, setJointsCsv] = useState('')
  const [solversJson, setSolversJson] = useState('{}')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setError(null)
    try {
      setPresets(await listPresets())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    if (open) refresh()
  }, [open, refresh])

  // Close on Escape for keyboard users (the backdrop click is mouse-only).
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const startCreate = () => {
    setEditing(emptyDraft())
    setEditingOriginalName(null)
    setJointsCsv('')
    setSolversJson('{}')
  }

  const startEdit = (p: PresetEntry) => {
    setEditing({ ...p })
    setEditingOriginalName(p.name)
    setJointsCsv(p.joint_names.join(', '))
    setSolversJson(JSON.stringify(p.solvers ?? {}, null, 2))
  }

  const handleSave = async () => {
    if (!editing) return
    setBusy(true)
    setError(null)
    try {
      const joint_names = jointsCsv.split(',').map(s => s.trim()).filter(Boolean)
      const solvers = JSON.parse(solversJson || '{}') as Record<string, unknown>
      const renameFrom =
        editingOriginalName && editingOriginalName !== editing.name
          ? editingOriginalName
          : undefined
      await upsertPreset({ ...editing, joint_names, solvers }, { renameFrom })
      setEditing(null)
      setEditingOriginalName(null)
      await refresh()
      onChanged?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const handleDelete = async (name: string) => {
    setBusy(true)
    setError(null)
    try {
      await deletePreset(name)
      setConfirmDelete(null)
      await refresh()
      onChanged?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const setAsset = (key: string, path: string) => {
    if (!editing) return
    const assets = { ...(editing.assets ?? {}) }
    if (path.trim()) assets[key] = path.trim()
    else delete assets[key]
    setEditing({ ...editing, assets })
  }

  const handleAssetFile = async (key: string, e: React.ChangeEvent<HTMLInputElement>) => {
    if (!editing) return
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ''
    setBusy(true)
    setError(null)
    try {
      const path = await uploadAsset(file, key)
      setAsset(key, path)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={onClose} role="presentation">
      <div
        className="bg-dam-surface border border-dam-border rounded-lg w-full max-w-2xl max-h-[90vh] flex flex-col"
        onClick={e => e.stopPropagation()}
        role="presentation"
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-dam-border">
          <h2 className="text-dam-text text-sm font-semibold">Robot Preset Manager</h2>
          <button onClick={onClose} className="text-dam-muted hover:text-dam-text"><X size={16} /></button>
        </div>

        {error && (
          <div className="px-4 py-2 bg-dam-red/10 border-b border-dam-red/30 text-dam-red text-xs">{error}</div>
        )}

        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {presets.length === 0 && !editing && (
            <p className="text-dam-muted text-xs italic text-center py-6">
              No presets registered yet. Click <span className="text-dam-blue">+ New preset</span> to add one.
            </p>
          )}

          {presets.map(p => (
            <div key={p.name} className="border border-dam-border rounded p-3 bg-dam-surface-2">
              <div className="flex items-start justify-between gap-2 mb-1">
                <div className="flex-1 min-w-0">
                  <p className="text-dam-text text-xs font-semibold font-mono">{p.name}</p>
                  <p className="text-dam-muted text-[10px] mt-0.5">
                    {p.joint_names.length} joints · {p.degrees_mode ? 'degrees' : 'radians'} mode
                  </p>
                  {Object.entries(p.assets ?? {}).map(([key, path]) => (
                    <p key={key} className="text-dam-muted text-[10px] font-mono mt-0.5 truncate">
                      {key}: {path}
                    </p>
                  ))}
                  {Object.keys(p.solvers ?? {}).length > 0 && (
                    <p className="text-dam-blue text-[10px] mt-0.5">
                      {Object.keys(p.solvers).length} solver{Object.keys(p.solvers).length === 1 ? '' : 's'}
                    </p>
                  )}
                </div>
                <div className="flex gap-1 shrink-0">
                  {confirmDelete === p.name ? (
                    <>
                      <button
                        onClick={() => handleDelete(p.name)}
                        disabled={busy}
                        className="px-2 py-1 rounded text-[10px] bg-dam-red/15 border border-dam-red/40 text-dam-red hover:bg-dam-red/25 transition-colors"
                      >
                        Confirm
                      </button>
                      <button
                        onClick={() => setConfirmDelete(null)}
                        disabled={busy}
                        className="px-2 py-1 rounded text-[10px] text-dam-muted hover:text-dam-text transition-colors"
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        onClick={() => startEdit(p)}
                        disabled={busy}
                        className="p-1.5 rounded text-dam-muted hover:text-dam-blue hover:bg-dam-blue/10 transition-colors"
                        title="Edit preset"
                      >
                        <Pencil size={12} />
                      </button>
                      <button
                        onClick={() => setConfirmDelete(p.name)}
                        disabled={busy}
                        className="p-1.5 rounded text-dam-muted hover:text-dam-red hover:bg-dam-red/10 transition-colors"
                        title="Delete preset"
                      >
                        <Trash2 size={12} />
                      </button>
                    </>
                  )}
                </div>
              </div>
            </div>
          ))}

          {editing && (
            <div className="border border-dam-blue/40 rounded p-3 bg-dam-blue/5 space-y-2">
              <div className="space-y-1">
                <label className="text-dam-muted text-[10px] uppercase tracking-wider">Name</label>
                <input
                  value={editing.name}
                  onChange={e => setEditing({ ...editing, name: e.target.value })}
                  className={`w-full ${inputCls}`}
                  placeholder="e.g. my_custom_arm"
                />
              </div>
              <div className="space-y-1">
                <label className="text-dam-muted text-[10px] uppercase tracking-wider">Joint names (comma-separated)</label>
                <input
                  value={jointsCsv}
                  onChange={e => setJointsCsv(e.target.value)}
                  className={`w-full ${inputCls}`}
                  placeholder="shoulder_pan, shoulder_lift, ..."
                />
              </div>
              <div className="flex items-center">
                <label className="flex items-center gap-2 text-xs text-dam-text">
                  <input
                    type="checkbox"
                    checked={editing.degrees_mode}
                    onChange={e => setEditing({ ...editing, degrees_mode: e.target.checked })}
                    className="accent-dam-blue"
                  />
                  Degrees mode
                </label>
              </div>
              <div className="space-y-2">
                <label className="text-dam-muted text-[10px] uppercase tracking-wider">Robot assets</label>
                {(['urdf', 'usd'] as const).map(key => (
                  <div key={key} className="flex gap-2 items-center">
                    <span className="w-10 text-dam-muted text-[10px] uppercase">{key}</span>
                    <input
                      value={editing.assets?.[key] ?? ''}
                      onChange={e => setAsset(key, e.target.value)}
                      className={`flex-1 ${inputCls}`}
                      placeholder={key === 'urdf' ? 'assets/robot.urdf or /abs/path/robot.urdf' : 'assets/scene.usd or /abs/path/scene.usd'}
                    />
                    <label className="flex items-center gap-1 px-2 py-1.5 bg-dam-surface-3 border border-dam-border rounded text-xs text-dam-muted hover:text-dam-text cursor-pointer transition-colors">
                      <Upload size={11} /> Upload
                      <input
                        type="file"
                        accept={key === 'urdf' ? '.urdf,.xml' : '.usd,.usda,.usdc'}
                        onChange={e => handleAssetFile(key, e)}
                        className="hidden"
                      />
                    </label>
                  </div>
                ))}
              </div>
              <div className="space-y-1">
                <label className="text-dam-muted text-[10px] uppercase tracking-wider">Solver definitions (JSON)</label>
                <textarea
                  value={solversJson}
                  onChange={e => setSolversJson(e.target.value)}
                  className={`w-full min-h-28 ${inputCls}`}
                  placeholder='{"arm": {"type": "pinocchio_kinematics", "capabilities": ["kinematics"], "params": {"asset_ref": "urdf"}}}'
                />
              </div>
              <div className="flex gap-2 justify-end pt-1">
                <button
                  onClick={() => { setEditing(null); setEditingOriginalName(null); setSolversJson('{}') }}
                  disabled={busy}
                  className="px-3 py-1 text-xs text-dam-muted hover:text-dam-text"
                >
                  Cancel
                </button>
                <button
                  onClick={handleSave}
                  disabled={busy || !editing.name.trim() || !jointsCsv.trim()}
                  className="flex items-center gap-1 px-3 py-1 bg-dam-blue-dim border border-dam-blue text-dam-blue rounded text-xs disabled:opacity-50 hover:bg-dam-blue/20 transition-colors"
                >
                  <Check size={11} /> Save
                </button>
              </div>
            </div>
          )}
        </div>

        {!editing && (
          <div className="px-4 py-3 border-t border-dam-border">
            <button
              onClick={startCreate}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-dam-blue-dim border border-dam-blue text-dam-blue rounded text-xs hover:bg-dam-blue/20 transition-colors"
            >
              <Plus size={12} /> New preset
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
