'use client'
import React, { useMemo, useRef, useEffect } from 'react'

export interface TimelineCycle {
  cycle_id: number
  timestamp_ns: number
  has_violation: boolean
  has_clamp: boolean
  violated_layer_mask: number
  clamped_layer_mask: number
}

export interface McapTimelineViewProps {
  readonly cycles: TimelineCycle[]
  readonly selectedCycleId?: number
  readonly onSelectCycle?: (cycleId: number) => void
}

type CycleStatus = 'reject' | 'clamp' | 'pass'

function cycleStatus(c: TimelineCycle): CycleStatus {
  if (c.violated_layer_mask > 0) return 'reject'
  if (c.clamped_layer_mask > 0) return 'clamp'
  return 'pass'
}

const STATUS_COLOR: Record<CycleStatus, string> = {
  reject: 'bg-red-500',
  clamp:  'bg-dam-blue',
  pass:   'bg-green-500/60',
}
const STATUS_HOVER: Record<CycleStatus, string> = {
  reject: 'hover:bg-red-400',
  clamp:  'hover:bg-blue-400',
  pass:   'hover:bg-green-400/80',
}

export function McapTimelineView({
  cycles,
  selectedCycleId,
  onSelectCycle,
}: McapTimelineViewProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const selectedBarRef = useRef<HTMLButtonElement | null>(null)

  // ── Precompute ─────────────────────────────────────────────────────────
  const statuses = useMemo(() => cycles.map(cycleStatus), [cycles])

  const incidentIds = useMemo(
    () =>
      cycles
        .filter((_, i) => statuses[i] !== 'pass')
        .map(c => c.cycle_id),
    [cycles, statuses]
  )

  const stats = useMemo(() => ({
    total:  cycles.length,
    pass:   statuses.filter(s => s === 'pass').length,
    clamp:  statuses.filter(s => s === 'clamp').length,
    reject: statuses.filter(s => s === 'reject').length,
  }), [cycles, statuses])

  const selectedIdx = cycles.findIndex(c => c.cycle_id === selectedCycleId)
  // Current incident position (1-based) among all incidents
  const incidentPos = selectedCycleId != null
    ? incidentIds.indexOf(selectedCycleId) + 1   // 0 if not an incident
    : 0

  // ── Incident navigation ────────────────────────────────────────────────
  function jumpIncident(dir: 'prev' | 'next') {
    if (incidentIds.length === 0 || !onSelectCycle) return
    if (selectedCycleId == null) {
      onSelectCycle(dir === 'next' ? incidentIds[0] : (incidentIds.at(-1) ?? incidentIds[0]))
      return
    }
    const pos = incidentIds.indexOf(selectedCycleId)
    if (dir === 'prev') {
      const target = pos <= 0 ? (incidentIds.at(-1) ?? incidentIds[0]) : incidentIds[pos - 1]
      onSelectCycle(target)
    } else {
      const target = pos < 0 || pos >= incidentIds.length - 1
        ? incidentIds[0]
        : incidentIds[pos + 1]
      onSelectCycle(target)
    }
  }

  // Scroll selected bar into view
  const isFirstRender = useRef(true)
  useEffect(() => {
    if (cycles.length === 0 || selectedCycleId == null) return

    // We use a small delay via requestAnimationFrame to ensure React has finished
    // painting the "isSelected" state to the DOM, so the ref is guaranteed to exist.
    const scrollTask = requestAnimationFrame(() => {
      if (!selectedBarRef.current) return

      const behavior = isFirstRender.current ? 'auto' : 'smooth'
      selectedBarRef.current.scrollIntoView({
        block: 'nearest',
        inline: 'start',
        behavior
      })
      isFirstRender.current = false
    })

    return () => cancelAnimationFrame(scrollTask)
  }, [selectedCycleId, cycles]) // Depend on full cycles array for reliable sync

  if (cycles.length === 0) {
    return (
      <div className="py-8 text-center text-dam-muted text-sm">
        No cycles recorded in this session.
      </div>
    )
  }

  return (
    <div className="space-y-1.5">
      {/* ── Stats + incident navigator ─────────────────────────────────── */}
      <div className="flex items-center gap-3 text-[10px] text-dam-muted">
        <span>
          pass <span className="font-mono font-bold text-green-500">{stats.pass}</span>
        </span>
        <span>
          clamp <span className="font-mono font-bold text-dam-blue">{stats.clamp}</span>
        </span>
        <span>
          reject <span className="font-mono font-bold text-red-500">{stats.reject}</span>
        </span>
        {selectedIdx >= 0 && (
          <span className="ml-auto font-mono text-dam-text">
            cycle #{cycles[selectedIdx].cycle_id} · {new Date(cycles[selectedIdx].timestamp_ns / 1_000_000).toLocaleTimeString([], {
              hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 2,
            })} · {statuses[selectedIdx]}
          </span>
        )}

        {/* Incident navigator */}
        {incidentIds.length > 0 && (
          <div className="flex items-center gap-1">
            <button
              onClick={() => jumpIncident('prev')}
              className="text-dam-muted hover:text-dam-text transition-colors"
              title="Previous incident"
            >
              Prev incident
            </button>
            <span className="text-dam-muted/60 select-none whitespace-nowrap">
              {incidentPos > 0
                ? <span><span className="font-bold text-dam-text">{incidentPos}</span> / {incidentIds.length}</span>
                : <span className="text-dam-muted/60">{incidentIds.length} incidents</span>
              }
            </span>
            <button
              onClick={() => jumpIncident('next')}
              className="text-dam-muted hover:text-dam-text transition-colors"
              title="Next incident"
            >
              Next
            </button>
          </div>
        )}
      </div>

      {/* ── Timeline strip ──────────────────────────────────────────────── */}
      <div className="relative">
        <div
          ref={containerRef}
          className="overflow-x-auto pb-1"
          style={{ scrollbarWidth: 'thin' }}
        >
          <div className="flex gap-px min-w-min px-4 py-1">
            {cycles.map((cycle, idx) => {
              const status = statuses[idx]
              const isSelected = cycle.cycle_id === selectedCycleId
              return (
                <button
                  key={cycle.cycle_id}
                  ref={isSelected ? selectedBarRef : undefined}
                  onClick={() => onSelectCycle?.(cycle.cycle_id)}
                  title={`Cycle ${cycle.cycle_id} — ${status.toUpperCase()}\n${new Date(cycle.timestamp_ns / 1_000_000).toLocaleTimeString()}`}
                  className={[
                    'shrink-0 rounded-sm transition-all duration-75 scroll-mx-6',
                    STATUS_COLOR[status],
                    STATUS_HOVER[status],
                    isSelected
                      ? 'w-4 h-10 ring-2 ring-white/80 ring-offset-1 ring-offset-dam-surface shadow-lg z-10 relative'
                      : 'w-2 h-8',
                  ].join(' ')}
                />
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
