'use client'
import React, { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { McapSessionDetail, McapSessionSummary } from '@/lib/api'
import { FileText, AlertTriangle, AlertCircle, Download, Loader2, Film, Activity, Archive } from 'lucide-react'

export interface McapSessionListProps {
  readonly onSelectSession?: (filename: string) => void
  readonly onDeleteSession?: (filename: string) => void
  readonly selectedFilename?: string
}

export function McapSessionList({
  onSelectSession,
  onDeleteSession,
  selectedFilename,
}: McapSessionListProps) {
  const [sessions, setSessions] = useState<McapSessionSummary[]>([])
  const [detailsMap, setDetailsMap] = useState<Record<string, McapSessionDetail>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [archiving, setArchiving] = useState(false)
  const [sessionToArchive, setSessionToArchive] = useState<string[] | null>(null)

  useEffect(() => {
    loadSessions()
  }, [])

  async function loadSessions() {
    try {
      setLoading(true)
      setError(null)
      const data = await api.listMcapSessions()
      const sessions = data?.sessions ?? []
      setSessions(sessions)
      setSelected(prev => new Set([...prev].filter(name => sessions.some(s => s.filename === name))))

      // Load details for each session in batches to avoid overwhelming the backend
      const detailsMap: Record<string, McapSessionDetail> = {}
      const batchSize = 5
      for (let i = 0; i < sessions.length; i += batchSize) {
        const batch = sessions.slice(i, i + batchSize)
        const batchDetails = await Promise.all(
          batch.map(s =>
            api.getMcapSession(s.filename)
              .catch(err => ({ filename: s.filename, error: err.message }))
          )
        )
        batchDetails.forEach(d => {
          if (d && 'stats' in d) detailsMap[d.filename] = d as McapSessionDetail
        })
        // Update state progressively so UI feels responsive
        setDetailsMap(prev => ({ ...prev, ...detailsMap }))
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load sessions')
    } finally {
      setLoading(false)
    }
  }

  async function archiveSessions(filenames: string[]) {
    if (filenames.length === 0) return
    setSessionToArchive(filenames)
  }

  async function executeArchive() {
    if (!sessionToArchive || sessionToArchive.length === 0) return
    const filenames = sessionToArchive
    setSessionToArchive(null)

    try {
      setArchiving(true)
      setError(null)
      if (filenames.length === 1) {
        await api.deleteMcapSession(filenames[0])
      } else {
        const result = await api.archiveMcapSessions(filenames)
        if (result.failed.length > 0) {
          throw new Error(`Failed to archive: ${result.failed.join(', ')}`)
        }
      }
      filenames.forEach(filename => onDeleteSession?.(filename))
      setSelected(prev => {
        const next = new Set(prev)
        filenames.forEach(filename => next.delete(filename))
        return next
      })
      await loadSessions()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to archive sessions')
    } finally {
      setArchiving(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8 text-dam-muted">
        <Loader2 size={16} className="animate-spin mr-2" />
        <span className="text-sm">Loading MCAP sessions...</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-4 bg-red-500/10 border border-red-500/20 rounded-lg text-red-600 text-sm">
        {error}
      </div>
    )
  }

  if (sessions.length === 0) {
    return (
      <div className="rounded-lg border border-dam-border/70 bg-dam-surface-1/40 p-3 space-y-3">
        <div className="flex items-center justify-between gap-2">
          <label className="flex items-center gap-2 text-xs text-dam-muted">
            <input
              type="checkbox"
              checked={false}
              disabled
              className="h-4 w-4 accent-dam-blue disabled:opacity-40"
            />
            Select all
          </label>
          <button
            type="button"
            disabled
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-dam-muted bg-dam-surface-2 border border-dam-border rounded-lg disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <Archive size={12} />
            Move selected
          </button>
        </div>
        <div className="flex min-h-[180px] flex-col items-center justify-center rounded-lg border border-dashed border-dam-border/70 bg-dam-surface-2/50 px-4 py-8 text-center text-dam-muted">
          <FileText size={32} className="mx-auto mb-2 opacity-50" />
          <p className="text-sm">No MCAP sessions recorded yet</p>
          <p className="mt-1 text-xs opacity-60">Start a run to create a session</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <label className="flex items-center gap-2 text-xs text-dam-muted">
          <input
            type="checkbox"
            checked={sessions.length > 0 && selected.size === sessions.length}
            onChange={e => setSelected(e.target.checked ? new Set(sessions.map(s => s.filename)) : new Set())}
            className="h-4 w-4 accent-dam-blue"
          />
          Select all
        </label>
        <button
          type="button"
          onClick={() => archiveSessions([...selected])}
          disabled={selected.size === 0 || archiving}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-dam-muted bg-dam-surface-2 border border-dam-border rounded-lg hover:text-dam-text hover:border-dam-blue/40 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {archiving ? <Loader2 size={12} className="animate-spin" /> : <Archive size={12} />}
          Move selected
        </button>
      </div>
      {sessions.map(session => {
        const details = detailsMap[session.filename]
        const isSelected = selectedFilename === session.filename
        const isChecked = selected.has(session.filename)
        const hasViolations = (details?.stats.violation_cycles ?? 0) > 0
        const hasClamps = (details?.stats.clamp_cycles ?? 0) > 0

        return (
          <button
            key={session.filename}
            type="button"
            onClick={() => onSelectSession?.(session.filename)}
            className={`p-4 rounded-lg border cursor-pointer transition-all duration-150 w-full text-left ${
              isSelected
                ? 'bg-dam-blue/10 border-dam-blue/40 shadow-sm'
                : 'bg-dam-surface-2 border-dam-border/60 hover:border-dam-blue/30 hover:bg-dam-surface-1'
            }`}
          >
            {/* Header: Icon + Filename + Size */}
            <div className="flex items-center gap-3 mb-3">
              <input
                aria-label={`Select ${session.filename}`}
                type="checkbox"
                checked={isChecked}
                onClick={e => e.stopPropagation()}
                onChange={e => {
                  setSelected(prev => {
                    const next = new Set(prev)
                    if (e.target.checked) next.add(session.filename)
                    else next.delete(session.filename)
                    return next
                  })
                }}
                className="h-4 w-4 accent-dam-blue shrink-0"
              />
              <div className={`p-2 rounded-lg ${isSelected ? 'bg-dam-blue/20' : 'bg-dam-surface-1'}`}>
                <Film size={16} className={isSelected ? 'text-dam-blue' : 'text-dam-muted'} />
              </div>
              <div className="flex-1 min-w-0">
                <p className="font-mono text-sm font-semibold text-dam-text truncate">
                  {session.filename}
                </p>
                <p className="text-xs text-dam-muted mt-0.5">
                  {new Date(session.created_at * 1000).toLocaleString()}
                </p>
              </div>
              <span className="text-xs font-mono text-dam-muted bg-dam-surface-1 px-2 py-1 rounded">
                {session.size_mb.toFixed(1)} MB
              </span>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  archiveSessions([session.filename])
                }}
                disabled={archiving}
                className="p-1.5 text-dam-muted hover:text-dam-blue hover:bg-dam-blue/10 rounded transition-colors disabled:opacity-40"
                title="Move to _trash"
              >
                <Archive size={14} />
              </button>
            </div>

            {/* Stats row */}
            {details && (
              <div className="grid grid-cols-3 gap-2 text-xs">
                <div className="flex items-center gap-1.5 bg-dam-surface-1/50 p-2 rounded-lg">
                  <Activity size={12} className="text-dam-blue shrink-0" />
                  <span className="text-dam-muted">Cycles</span>
                  <span className="font-mono font-bold text-dam-text ml-auto">
                    {details.stats.total_cycles}
                  </span>
                </div>
                <div className={`flex items-center gap-1.5 bg-dam-surface-1/50 p-2 rounded-lg ${hasViolations ? 'border border-red-500/20' : ''}`}>
                  {hasViolations && <AlertTriangle size={12} className="text-red-500 shrink-0" />}
                  {!hasViolations && <AlertTriangle size={12} className="text-dam-muted/50 shrink-0" />}
                  <span className="text-dam-muted">Violations</span>
                  <span className={`font-mono font-bold ml-auto ${hasViolations ? 'text-red-500' : 'text-dam-muted'}`}>
                    {details.stats.violation_cycles}
                  </span>
                </div>
                <div className={`flex items-center gap-1.5 bg-dam-surface-1/50 p-2 rounded-lg ${hasClamps ? 'border border-yellow-500/20' : ''}`}>
                  {hasClamps && <AlertCircle size={12} className="text-yellow-500 shrink-0" />}
                  {!hasClamps && <AlertCircle size={12} className="text-dam-muted/50 shrink-0" />}
                  <span className="text-dam-muted">Clamps</span>
                  <span className={`font-mono font-bold ml-auto ${hasClamps ? 'text-yellow-500' : 'text-dam-muted'}`}>
                    {details.stats.clamp_cycles}
                  </span>
                </div>
              </div>
            )}

            {/* Cameras + Layers */}
            {details && (
              <div className="mt-3 pt-3 border-t border-dam-border/30 space-y-1 text-xs">
                {details.stats.cameras && details.stats.cameras.length > 0 && (
                  <p className="text-dam-muted">
                    <span className="text-dam-text/70 font-medium">Cameras:</span>{' '}
                    <span className="font-mono">{details.stats.cameras.join(', ')}</span>
                  </p>
                )}
                {details.stats.violated_layers && details.stats.violated_layers.length > 0 && (
                  <p className="text-red-500">
                    <span className="text-dam-text/70 font-medium">Violated Layers:</span>{' '}
                    <span className="font-mono">{details.stats.violated_layers.join(', ')}</span>
                  </p>
                )}
              </div>
            )}

            {/* Download button */}
            <div className="mt-3 pt-3 border-t border-dam-border/30 flex items-center justify-between">
              <span className="text-xs text-dam-muted">Duration: {details?.stats.duration_sec ?? 0}s</span>
              <a
                href={api.mcapDownloadUrl(session.filename)}
                onClick={e => e.stopPropagation()}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-dam-blue bg-dam-blue/10 hover:bg-dam-blue/20 rounded-lg transition-colors"
              >
                <Download size={12} />
                Download MCAP
              </a>
            </div>
          </button>
        )
      })}

      {sessionToArchive && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <button
            type="button"
            aria-label="Close"
            className="absolute inset-0 bg-black/60 backdrop-blur-sm transition-opacity"
            onClick={() => setSessionToArchive(null)}
          />
          <div className="relative bg-dam-surface border border-dam-border rounded-2xl w-full max-w-md shadow-2xl flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-150">
            <div className="flex items-center gap-2.5 p-4 border-b border-dam-border bg-dam-surface-2/50">
              <AlertTriangle size={18} className="text-red-500 shrink-0" />
              <h3 className="text-sm font-bold text-dam-text uppercase tracking-widest">Delete Session</h3>
            </div>

            <div className="p-5 text-xs text-dam-muted space-y-2 leading-relaxed">
              <p>
                Are you sure you want to permanently delete{' '}
                <span className="font-mono text-dam-text font-bold bg-dam-surface-3 px-1.5 py-0.5 rounded border border-dam-border">
                  {sessionToArchive.length === 1 ? sessionToArchive[0] : `${sessionToArchive.length} sessions`}
                </span>?
              </p>
              <p className="opacity-80">
                This action is permanent and cannot be undone. All recorded cycle metrics and video frames will be deleted from disk.
              </p>
            </div>

            <div className="p-4 border-t border-dam-border bg-dam-surface-2/30 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setSessionToArchive(null)}
                className="px-4 py-2 bg-dam-surface-2 hover:bg-dam-surface-3 border border-dam-border text-dam-text text-xs font-bold rounded-lg transition-colors cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={executeArchive}
                className="px-4 py-2 bg-red-600 hover:bg-red-500 text-white text-xs font-bold rounded-lg transition-colors shadow-lg shadow-red-600/10 flex items-center gap-1.5 cursor-pointer"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
