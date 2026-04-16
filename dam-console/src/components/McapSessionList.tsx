'use client'
import React, { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { McapSessionDetail, McapSessionSummary } from '@/lib/api'
import { FileText, AlertTriangle, AlertCircle, Download, Loader2, Film, Activity, Trash2 } from 'lucide-react'

export interface McapSessionListProps {
  onSelectSession?: (filename: string) => void
  onDeleteSession?: (filename: string) => void
  selectedFilename?: string
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
      <div className="py-8 text-center text-dam-muted">
        <FileText size={32} className="mx-auto mb-2 opacity-50" />
        <p className="text-sm">No MCAP sessions recorded yet</p>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {sessions.map(session => {
        const details = detailsMap[session.filename]
        const isSelected = selectedFilename === session.filename
        const hasViolations = (details?.stats.violation_cycles ?? 0) > 0
        const hasClamps = (details?.stats.clamp_cycles ?? 0) > 0

        return (
          <div
            key={session.filename}
            onClick={() => onSelectSession?.(session.filename)}
            className={`p-4 rounded-lg border cursor-pointer transition-all duration-150 ${
              isSelected
                ? 'bg-dam-blue/10 border-dam-blue/40 shadow-sm'
                : 'bg-dam-surface-2 border-dam-border/60 hover:border-dam-blue/30 hover:bg-dam-surface-1'
            }`}
          >
            {/* Header: Icon + Filename + Size */}
            <div className="flex items-center gap-3 mb-3">
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
                  if (confirm(`Completely delete session ${session.filename}?`)) {
                    api.deleteMcapSession(session.filename).then(() => {
                      onDeleteSession?.(session.filename)
                      loadSessions()
                    })
                  }
                }}
                className="p-1.5 text-dam-muted hover:text-red-500 hover:bg-red-500/10 rounded transition-colors"
                title="Delete Session"
              >
                <Trash2 size={14} />
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
          </div>
        )
      })}
    </div>
  )
}
