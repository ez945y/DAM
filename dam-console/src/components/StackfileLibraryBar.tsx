'use client'
import { Plus, Pencil, Trash2, AlertCircle, Check } from 'lucide-react'
import type { StackfileLibrary } from '@/hooks/useStackfileLibrary'

const selCls =
  'bg-dam-surface-2 border border-dam-border rounded px-2 py-1.5 text-xs font-mono text-dam-text'

/**
 * Shared stackfile-library selector used by both the Config and Guard pages.
 * The selected target (live vs a named library config) is synced via the
 * hook so switching pages keeps the same context.
 */
export function StackfileLibraryBar({
  lib,
  getYaml,
  onLoaded,
  disabled,
}: {
  readonly lib: StackfileLibrary
  readonly getYaml: () => string
  readonly onLoaded: (yaml: string) => void
  readonly disabled?: boolean
}) {
  const isLive = lib.target === lib.LIVE
  const off = disabled || lib.busy

  return (
    <div className="glass-card p-6 space-y-3">
      <h2 className="text-dam-muted text-xs uppercase tracking-widest font-semibold relative z-10">
        Stackfile
      </h2>
      <div className="relative z-10 space-y-3">
        <p className="text-dam-muted text-[10px]">
          Edit the live config, or pick a saved one from the library. Library edits
          stay isolated — use Apply &amp; Restart to push the selected config to the
          running system.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={lib.target}
            disabled={off}
            onChange={(e) => lib.switchTarget(e.target.value, onLoaded)}
            className={`${selCls} min-w-[16rem]`}
          >
            <option value={lib.LIVE}>● Live · .dam_stackfile.yaml (running)</option>
            {lib.libNames.length > 0 && <option disabled>──────────</option>}
            {lib.libNames.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>

          <button
            onClick={() => lib.create(getYaml(), onLoaded)}
            disabled={off}
            className="flex items-center gap-1 px-2.5 py-1.5 bg-dam-surface-3 border border-dam-border text-dam-muted text-xs rounded hover:text-dam-text transition-colors disabled:opacity-50"
          >
            <Plus size={11} /> New
          </button>
          <button
            onClick={() => lib.rename()}
            disabled={off || isLive}
            className="flex items-center gap-1 px-2.5 py-1.5 bg-dam-surface-3 border border-dam-border text-dam-muted text-xs rounded hover:text-dam-text transition-colors disabled:opacity-40"
          >
            <Pencil size={11} /> Rename
          </button>
          <button
            onClick={() => lib.del(onLoaded)}
            disabled={off || isLive}
            className="flex items-center gap-1 px-2.5 py-1.5 bg-dam-surface-3 border border-dam-border text-dam-muted text-xs rounded hover:text-dam-red hover:border-dam-red/40 transition-colors disabled:opacity-40"
          >
            <Trash2 size={11} /> Delete
          </button>
        </div>
        {!isLive && (
          <p className="text-dam-orange text-[10px]">
            Editing library config <span className="font-mono">{lib.target}</span> — the
            running system is unaffected until you press Apply &amp; Restart.
          </p>
        )}
        {lib.error && (
          <p className="flex items-center gap-1 text-dam-red text-[10px]">
            <AlertCircle size={10} /> {lib.error}
          </p>
        )}
        {lib.ok && (
          <p className="flex items-center gap-1 text-dam-green text-[10px]">
            <Check size={10} /> {lib.ok}
          </p>
        )}
      </div>
    </div>
  )
}
