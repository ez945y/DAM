'use client'
import React, { useState, useEffect } from 'react'
import { api } from '@/lib/api'
import { RiskBadge } from '@/components/RiskBadge'
import type { RiskEvent, RiskLevel, RiskLogStats } from '@/lib/types'
import { Download, ChevronRight, ChevronDown, Activity, Shield, Hash, Clock } from 'lucide-react'

export function RiskLogTable() {
  const [events, setEvents] = useState<RiskEvent[]>([])
  const [stats, setStats] = useState<RiskLogStats | null>(null)
  const [loading, setLoading] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [filters, setFilters] = useState({
    min_risk_level: '',
    rejected_only: false,
    clamped_only: false,
    limit: 200,
  })
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set())

  const toggleExpand = (key: string) => {
    setExpandedKeys(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const load = async (isBackground = false) => {
    if (!isBackground) setLoading(true)
    try {
      const [evRes, stRes] = await Promise.all([
        api.getRiskLog({
          min_risk_level: filters.min_risk_level || undefined,
          rejected_only: filters.rejected_only,
          clamped_only: filters.clamped_only,
          limit: filters.limit,
        }),
        api.getRiskLogStats(),
      ])
      setEvents(evRes.events)
      setStats(stRes)
    } catch { /* ignore */ } finally {
      if (!isBackground) setLoading(false)
    }
  }

  // Listen for global live refresh toggle
  useEffect(() => {
    const sync = () => {
      const saved = localStorage.getItem('dam_live_refresh')
      setAutoRefresh(saved !== 'false') // default to true
    }
    sync()
    window.addEventListener('dam_live_refresh_change', sync)
    return () => window.removeEventListener('dam_live_refresh_change', sync)
  }, [])

  // Auto-refresh effect
  useEffect(() => {
    void load()
    if (!autoRefresh) return
    const timer = setInterval(() => void load(true), 1000)
    return () => clearInterval(timer)
  }, [autoRefresh, filters])

  // Grouping logic: collapse consecutive identical results
  const groupEvents = (raw: RiskEvent[]) => {
    if (raw.length === 0) return []
    const groups: (RiskEvent & { count: number })[] = []
    
    for (const ev of raw) {
      if (groups.length === 0) {
        groups.push({ ...ev, count: 1 })
        continue
      }
      
      const last = groups[groups.length - 1]
      const isSame = 
        last.risk_level === ev.risk_level &&
        last.was_rejected === ev.was_rejected &&
        last.was_clamped === ev.was_clamped &&
        last.fallback_triggered === ev.fallback_triggered
      
      if (isSame) {
        last.count++
        // Keep the first (newest) event's display fields intact; just increment count
      } else {
        groups.push({ ...ev, count: 1 })
      }
    }
    return groups
  }

  const groupedEvents = groupEvents(events)

  return (
    <div className="space-y-4">
      {/* Stats summary */}
      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { label: 'Total Events', value: stats.total },
            { label: 'Rejected', value: stats.rejected },
            { label: 'Clamped', value: stats.clamped },
            { label: 'Avg Latency', value: stats.avg_latency_ms != null ? `${stats.avg_latency_ms.toFixed(1)}ms` : '—' },
          ].map(s => (
            <div key={s.label} className="bg-dam-surface-2 border border-dam-border rounded-lg px-3 py-2">
              <p className="text-dam-muted text-[10px] uppercase tracking-wider">{s.label}</p>
              <p className="text-xl font-bold text-dam-blue">{s.value}</p>
            </div>
          ))}
        </div>
      )}

      {/* Filter + export bar */}
      <div className="flex flex-wrap items-center gap-3 bg-dam-surface-1 p-2 border border-dam-border rounded">
        <select
          value={filters.min_risk_level}
          onChange={e => setFilters(f => ({ ...f, min_risk_level: e.target.value }))}
          className="bg-dam-surface-2 border border-dam-border text-dam-text text-xs rounded px-2 py-1.5"
        >
          <option value="">All risk levels</option>
          <option value="ELEVATED">≥ ELEVATED</option>
          <option value="CRITICAL">≥ CRITICAL</option>
          <option value="EMERGENCY">EMERGENCY only</option>
        </select>
        
        <div className="h-6 w-px bg-dam-border/40 mx-1" />

        <label className="flex items-center gap-1.5 text-xs text-dam-muted cursor-pointer">
          <input
            type="checkbox"
            checked={filters.rejected_only}
            onChange={e => setFilters(f => ({ ...f, rejected_only: e.target.checked }))}
            className="accent-dam-blue"
          />
          Rejected
        </label>
        <label className="flex items-center gap-1.5 text-xs text-dam-muted cursor-pointer">
          <input
            type="checkbox"
            checked={filters.clamped_only}
            onChange={e => setFilters(f => ({ ...f, clamped_only: e.target.checked }))}
            className="accent-dam-blue"
          />
          Clamped
        </label>

        <div className="h-6 w-px bg-dam-border/40 mx-1" />

        <div className="ml-auto flex gap-2">
          <a
            href={api.exportRiskLogJsonUrl()}
            download="risk_log.json"
            className="invisible sm:visible flex items-center gap-1 px-2 py-1.5 bg-dam-surface-2 border border-dam-border text-dam-muted text-xs rounded hover:text-dam-text transition-colors"
          >
            <Download size={11} /> JSON
          </a>
          <a
            href={api.exportRiskLogCsvUrl()}
            download="risk_log.csv"
            className="invisible sm:visible flex items-center gap-1 px-2 py-1.5 bg-dam-surface-2 border border-dam-border text-dam-muted text-xs rounded hover:text-dam-text transition-colors"
          >
            <Download size={11} /> CSV
          </a>
          <button
            onClick={() => void load()}
            disabled={loading}
            className="px-4 py-1.5 bg-dam-blue text-white text-xs font-bold rounded hover:bg-dam-blue-bright disabled:opacity-50 transition-colors"
          >
            {loading ? '...' : 'Reload'}
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto border border-dam-border rounded-lg bg-dam-surface-1">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-dam-surface-2 border-b border-dam-border text-dam-muted">
              <th className="w-8 px-4 py-2"></th>
              <th className="text-left px-4 py-2 font-semibold uppercase tracking-wider">ID</th>
              <th className="text-left px-4 py-2 font-semibold uppercase tracking-wider">Time</th>
              <th className="text-left px-4 py-2 font-semibold uppercase tracking-wider">Cycle</th>
              <th className="text-left px-4 py-2 font-semibold uppercase tracking-wider">Risk</th>
              <th className="text-left px-4 py-2 font-semibold uppercase tracking-wider">Outcome</th>
              <th className="text-left px-4 py-2 font-semibold uppercase tracking-wider">Details</th>
              <th className="text-left px-4 py-2 font-semibold uppercase tracking-wider">Lat</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-dam-border/40">
            {groupedEvents.length === 0 ? (
              <tr><td colSpan={8} className="text-center text-dam-muted py-12">No risk events recorded.</td></tr>
            ) : groupedEvents.map((e, i) => {
              const groupKey = `${i}:${e.risk_level}-${e.was_rejected}-${e.was_clamped}-${e.fallback_triggered ?? ''}`
              const isExpanded = expandedKeys.has(groupKey)
              return (
                <React.Fragment key={groupKey}>
                  <tr
                    onClick={() => toggleExpand(groupKey)}
                    className={`hover:bg-dam-surface-2/60 transition-colors group cursor-pointer border-l-2 ${
                      isExpanded ? 'bg-dam-surface-2 border-dam-blue' : 'border-transparent'
                    }`}
                  >
                    <td className="px-4 py-3 text-center text-dam-muted">
                      {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    </td>
                    <td className="px-4 py-3 text-dam-muted font-mono">{e.event_id}</td>
                    <td className="px-4 py-3 font-mono text-dam-muted">{new Date(e.timestamp * 1000).toLocaleTimeString()}</td>
                    <td className="px-4 py-3 font-mono text-dam-text">{e.cycle_id}</td>
                    <td className="px-4 py-3"><RiskBadge level={e.risk_level as RiskLevel} /></td>
                    <td className="px-4 py-3 whitespace-nowrap">
                      <div className="flex items-center gap-2">
                        {e.was_rejected && <span className="px-1.5 py-0.5 rounded bg-orange-950/40 text-dam-orange border border-orange-900/40 font-bold text-[10px]">REJECTED</span>}
                        {e.was_clamped && <span className="px-1.5 py-0.5 rounded bg-blue-950/40 text-dam-blue border border-blue-900/40 font-bold text-[10px]">CLAMPED</span>}
                        {!e.was_rejected && !e.was_clamped && <span className="text-dam-muted">—</span>}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1.5">
                        <span className={`font-mono truncate max-w-[120px] text-[10px] ${e.fallback_triggered ? 'text-dam-orange' : 'text-dam-muted'}`}>
                          {e.fallback_triggered ? `Fallback: ${e.fallback_triggered}` : (e.guard_results?.[0]?.reason || '—')}
                        </span>
                        {e.count > 1 && (
                          <span className="px-1.5 py-0.5 rounded-full bg-dam-surface-3 border border-dam-border text-[10px] text-dam-blue font-bold">
                            ×{e.count}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 font-mono text-dam-muted">{e.latency_ms['total']?.toFixed(1) ?? '—'}<span className="text-[9px] opacity-60">ms</span></td>
                  </tr>
                  
                  {isExpanded && (
                    <tr className="bg-dam-surface-2/40 animate-in slide-in-from-top-1 duration-200">
                      <td colSpan={8} className="px-8 py-4 border-l-2 border-dam-blue">
                        <div className="space-y-4">
                          {/* Header section */}
                          <div className="flex flex-wrap gap-4 text-[10px] text-dam-muted uppercase tracking-widest border-b border-dam-border/40 pb-2">
                            <div className="flex items-center gap-1.5"><Hash size={12} /> Trace: <span className="text-dam-text font-mono select-all uppercase">{e.trace_id}</span></div>
                            <div className="flex items-center gap-1.5"><Clock size={12} /> Full Time: <span className="text-dam-text font-mono">{new Date(e.timestamp * 1000).toISOString()}</span></div>
                          </div>

                          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            {/* Guard Decisions List (The "Wireshark Packet details") */}
                            <div className="space-y-2">
                              <h4 className="flex items-center gap-2 text-xs font-bold text-dam-blue uppercase tracking-wider mb-2">
                                <Shield size={14} /> Guard Enforcement Details
                              </h4>
                              <div className="space-y-1.5">
                                {e.guard_results && e.guard_results.length > 0 ? e.guard_results.map((g, i) => (
                                  <div key={i} className="group/item flex flex-col p-2 bg-dam-surface-3 border border-dam-border rounded hover:border-dam-blue/30 transition-colors">
                                    <div className="flex items-center justify-between">
                                      <div className="flex items-center gap-2">
                                        <span className="text-[10px] bg-dam-surface-1 px-1 rounded border border-dam-border text-dam-muted">{g.layer}</span>
                                        <span className="text-xs font-bold text-dam-text font-mono">{g.name}</span>
                                      </div>
                                      <span className={`text-[10px] font-bold px-1.5 rounded-sm ${
                                        g.decision === 'PASS' ? 'text-dam-green' : 
                                        g.decision === 'CLAMP' ? 'text-dam-blue' : 'text-dam-orange'
                                      }`}>
                                        {g.decision}
                                      </span>
                                    </div>
                                    <div className="mt-1 pl-1 border-l border-dam-border/60 ml-2">
                                      <p className="text-[11px] text-dam-muted font-mono leading-relaxed whitespace-pre-wrap">
                                        {g.reason || 'Safety check passed.'}
                                      </p>
                                    </div>
                                  </div>
                                )) : (
                                  <p className="text-xs text-dam-muted italic">No specific guard results logged.</p>
                                )}
                              </div>
                            </div>

                            {/* Performance Breakdown */}
                            <div className="space-y-2">
                              <h4 className="flex items-center gap-2 text-xs font-bold text-dam-orange uppercase tracking-wider mb-2">
                                <Activity size={14} /> Frame Processing Latency
                              </h4>
                              <div className="space-y-1 p-2 bg-dam-surface-3 border border-dam-border rounded font-mono">
                                {Object.entries(e.latency_ms).sort((a,b) => b[1] - a[1]).map(([k, v]) => (
                                  <div key={k} className="flex items-center justify-between text-[11px] py-0.5 border-b border-dam-border/20 last:border-0">
                                    <span className="text-dam-muted">{k}</span>
                                    <span className={k === 'total' ? 'text-dam-orange font-bold' : 'text-dam-text'}>
                                      {v.toFixed(3)} ms
                                    </span>
                                  </div>
                                ))}
                              </div>
                              {e.fallback_triggered && (
                                <div className="mt-4 p-2 bg-dam-orange/10 border border-dam-orange/30 rounded">
                                  <p className="text-[10px] font-bold text-dam-orange uppercase">Active Fallback</p>
                                  <p className="text-xs text-dam-orange font-mono font-bold mt-1">➔ {e.fallback_triggered}</p>
                                </div>
                              )}
                            </div>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
