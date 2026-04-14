export type RiskLevel = 'NORMAL' | 'ELEVATED' | 'CRITICAL' | 'EMERGENCY'

/**
 * Point-in-time performance snapshot attached to each `cycle` WebSocket event
 * when the backend MetricBus is wired into TelemetryService.
 *
 * All latency values are in milliseconds.
 */
export interface PerfSnapshot {
  /** Pipeline-stage breakdown: source / policy / guards / sink / total */
  stages:      Record<string, number>
  /** Per-layer guard latency sums for the last committed cycle.
   *  Keys are "L0" … "L4" (only layers that executed are present). */
  layers:      Record<string, number>
  /** Per-guard latest latency. */
  guards:      Record<string, number>
  /** Control-loop cycle budget in ms. */
  deadline_ms: number
  /** Remaining headroom before the deadline: deadline_ms − total_ms. */
  slack_ms:    number
}
export type GuardDecision = 'PASS' | 'CLAMP' | 'REJECT' | 'FAULT'
export type RuntimeState = 'idle' | 'running' | 'paused' | 'stopped' | 'emergency'

export interface GuardStatus {
  name: string
  layer: string
  decision: GuardDecision
  reason: string
}

export interface CycleEvent {
  type: 'cycle' | 'ping'
  cycle_id: number
  trace_id: string
  was_clamped: boolean
  was_rejected: boolean
  risk_level: RiskLevel
  fallback_triggered: string | null
  latency_ms: Record<string, number>
  active_task?: string | null
  active_boundaries?: string[]
  guard_statuses: GuardStatus[]
  timestamp: number
  /** Present when the backend MetricBus is wired into TelemetryService. */
  perf?: PerfSnapshot
}

export interface RiskEvent {
  event_id: number
  timestamp: number
  cycle_id: number
  trace_id: string
  risk_level: RiskLevel
  was_clamped: boolean
  was_rejected: boolean
  fallback_triggered: string | null
  guard_results: GuardStatus[]
  latency_ms: Record<string, number>
  /** MetricBus snapshot captured at cycle boundary. Present when backend MetricBus is wired. */
  perf?: PerfSnapshot | null
}

export interface RiskLogStats {
  total: number
  rejected: number
  clamped: number
  by_risk_level: Record<string, number>
  avg_latency_ms: number | null
}

export interface BoundaryConfig {
  name: string
  layer?: string
  type: 'single' | 'list' | 'graph'
  loop?: boolean
  nodes: Array<{
    node_id: string
    fallback?: string
    timeout_sec?: number | null
    constraint?: Record<string, unknown>
  }>
}

export type EnforcementMode = 'enforce' | 'monitor' | 'log_only'

export interface JointDef {
  name: string
  lower_rad: number
  upper_rad: number
}

export interface UsbDeviceInfo {
  path: string
  type: 'serial' | 'video'
  label: string
  selected: boolean  // user-checked
}

export interface PolicyConfig {
  type: 'act' | 'diffusion' | 'smolvla' | 'noop'
  pretrained_path: string
  policy_id?: string
  device: 'cpu' | 'cuda' | 'mps'
  // Diffusion-specific inference params
  noise_scheduler_type?: string   // e.g. 'DDIM'
  num_inference_steps?: number    // e.g. 15
}

export interface TaskDef {
  id: string
  name: string
  description: string
  boundaries: string[]   // boundary container names
}

export interface ConstraintNodeDef {
  node_id: string
  // All constraint parameters (bounds, max_speed, …) live in params.
  params: Record<string, any>
  callback: string | null
  fallback: string
  timeout_sec: number | null
}

export interface BoundaryDef {
  name: string
  layer: string
  type: 'single' | 'list'
  nodes: ConstraintNodeDef[]
}

export interface RuntimeStatus {
  state: RuntimeState
  cycle_count: number
  error: string | null
  /** Set when hardware/startup validation failed at server boot. Blocks Start. */
  startup_error?: string | null
  has_runtime: boolean
  active_task?: string | null
  active_boundaries?: string[]
  control_frequency_hz?: number
  available_tasks?: string[]
  planned_task?: string | null
  planned_boundaries?: string[]
}

export interface LogEntry {
  type: GuardDecision | 'info'
  message: string
  timestamp: number
}

export interface TelemetrySnapshot {
  connected: boolean
  lastCycle: CycleEvent | null
  guardMap: Record<string, GuardStatus>
  latencyHistory: number[]
  /** Cycle IDs corresponding 1:1 to latencyHistory entries for click-through navigation. */
  latencyCycleIds: number[]
  /** Latest perf snapshot, present when backend MetricBus is active. */
  latestPerf: PerfSnapshot | null
  totalCycles: number
  totalRejects: number
  totalClamps: number
  totalFaults: number
  windowCycles: number   // cycles in last 1m
  windowRejects: number  // rejects in last 1m
  windowClamps: number   // clamps in last 1m
  events: LogEntry[]
}
