// Schema-based recursive YAML generator & Parser
// ──────────────────────────────────────────────────────────────────────────

import type {
  EnforcementMode,
  JointDef,
  PolicyConfig,
  TaskDef,
  BoundaryDef,
  ConstraintNodeDef,
  FallbackDef,
} from './types'

// ── Re-export types ──────────────────────────────────────────────────────────
export type { EnforcementMode, JointDef, PolicyConfig, TaskDef, BoundaryDef, ConstraintNodeDef, FallbackDef }

export type LoopbackConfig = {
  backend: 'mcap' | 'pickle'
  output_dir: string
  window_sec: number
  rotate_mb: number
  rotate_minutes: number
  max_queue_depth: number
  capture_images_on_clamp: boolean
}

export type CameraConfig = {
  name: string
  source_type: 'opencv' | 'udp'
  index?: number
  udp_url?: string
  width: number
  height: number
  fps: number
}

export interface DamConfig {
  templateId: string
  hardware_preset: string
  /** Action/observation space: per-joint targets or end-effector pose.
   *  Backend requires hardware.input_space === policy.input_space, so the
   *  console keeps a single value and emits it into both blocks. */
  input_space: 'joint' | 'ee'
  adapter: 'lerobot' | 'ros2' | 'simulation'
  lerobot_port: string
  lerobot_robot_type: string
  lerobot_robot_id: string
  lerobot_cameras: CameraConfig[]
  lerobot_calibration_path: string
  lerobot_degrees_mode: boolean
  ros2JointTopic: string
  ros2CmdTopic: string
  policy: PolicyConfig
  joints: JointDef[]
  controlFrequencyHz: number
  enforcement_mode: EnforcementMode
  fallbacks: FallbackDef[]
  guardsEnabled: Partial<Record<'ood' | 'motion' | 'execution' | 'hardware', boolean>>
  guardRouting: Partial<Record<'ood' | 'motion' | 'execution' | 'hardware', { phase?: number; always?: boolean; timeout_ms?: number }>>
  tasks: TaskDef[]
  boundaries: BoundaryDef[]
  loopback?: LoopbackConfig
  simulation_dataset_repo_id?: string
  simulation_episode?: number
  dataset_replay_to_hardware?: boolean
  dataset_image_namespace?: string
  observation_channels: string[]
  /** Optional override map: channel name → ROS2/MCAP topic.  Empty / missing
   *  entries fall back to the adapter's default topic for that channel. */
  channel_topic_overrides?: Record<string, string>
}

export interface TemplatePreset {
  id: string
  label: string
  description: string
  badge: string
  config: Partial<DamConfig>
}

// ─────────────────────────────────────────────────────────────────────────────
// Schema node types
// ─────────────────────────────────────────────────────────────────────────────

type ScalarNode = {
  kind: 'scalar'
  key: string
  value: (cfg: DamConfig) => string | number | boolean | null | undefined
}

type BlockNode = {
  kind: 'block'
  key: string
  children: YamlSection[]
  when?: (cfg: DamConfig) => boolean
}

type ListNode = {
  kind: 'list'
  key: string
  items: (cfg: DamConfig) => string[][]
  when?: (cfg: DamConfig) => boolean
}

type CustomNode = {
  kind: 'custom'
  lines: (cfg: DamConfig, indent: string) => string[]
  when?: (cfg: DamConfig) => boolean
}

type BlankNode = { kind: 'blank' }

type YamlSection = ScalarNode | BlockNode | ListNode | CustomNode | BlankNode

// ─────────────────────────────────────────────────────────────────────────────
// Presets DATA
// ─────────────────────────────────────────────────────────────────────────────

const SO101_JOINTS: JointDef[] = [
  { name: 'shoulder_pan' },
  { name: 'shoulder_lift' },
  { name: 'elbow_flex' },
  { name: 'wrist_flex' },
  { name: 'wrist_roll' },
  { name: 'gripper' },
]

const SO101_CAMERAS: CameraConfig[] = [
  { name: 'top',   source_type: 'opencv', index: 0, width: 640, height: 480, fps: 30 },
  { name: 'wrist', source_type: 'opencv', index: 1, width: 640, height: 480, fps: 30 },
]

const SO101_HEALTH_CHANNELS = ['current', 'temperature', 'voltage']
// Boundary defaults — factory limits for SO-101, in degrees.
// These are boundary config, NOT robot identity.  Robot identity is joint names only.
const SO101_USE_DEGREES = true
const SO101_UPPER = [104.5247, 100.2676, 105.0002, 103.5163, 176.1330, 99.9983]  // deg
const SO101_LOWER = [-104.5247, -100.2676, -105.0002, -103.5163, -176.1330, 0]   // deg
const SO101_MAX_VELOCITIES = Array(6).fill(Number((1.5 * 180 / Math.PI).toFixed(4)))  // 1.5 rad/s → deg/s

const LEFT_PICK_ZONE = [[-0.175, -0.025], [-0.075, 0.075], [0.075, 0.225]]
const RIGHT_PLACE_ZONE = [[0.025, 0.175], [-0.075, 0.075], [0.075, 0.225]]

// Shared L1 + L3 baseline. Sensors unavailable on a given adapter pass cleanly.
const BASE_BOUNDARIES: BoundaryDef[] = [
  {
    name: 'workspace', layer: 'L1', type: 'single',
    nodes: [{ node_id: 'default', params: { bounds: [[-0.4, 0.4], [-0.4, 0.4], [0.02, 0.6]] }, callback: 'workspace' }]
  },
  {
    name: 'joint_position_limits', layer: 'L1', type: 'single',
    nodes: [{ node_id: 'default', params: { upper: SO101_UPPER, lower: SO101_LOWER, use_degrees: SO101_USE_DEGREES }, callback: 'joint_position_limits' }]
  },
  {
    name: 'joint_velocity_limit', layer: 'L1', type: 'single',
    nodes: [{ node_id: 'default', params: { max_velocities: SO101_MAX_VELOCITIES, use_degrees: SO101_USE_DEGREES }, callback: 'joint_velocity_limit' }]
  },
  {
    name: 'ee_velocity_limit', layer: 'L1', type: 'single',
    nodes: [{ node_id: 'default', params: { max_ee_velocity: 0.5 }, callback: 'ee_velocity_limit' }]
  },
  {
    name: 'hardware_watchdog', layer: 'L3', type: 'single',
    nodes: [{
      node_id: 'default',
      params: {
        max_staleness_ms: 1000,
      },
      callback: 'hardware_watchdog',
      fallback: 'emergency_stop',
      timeout_sec: null,
      warn_frames: 3,
    }]
  },
  {
    name: 'temperature_limit', layer: 'L3', type: 'single',
    nodes: [{ node_id: 'default', params: { max_temperature_c: 55 }, callback: 'temperature_limit', fallback: 'slow_down', timeout_sec: null, warn_frames: 5 }]
  },
  {
    name: 'current_limit', layer: 'L3', type: 'single',
    nodes: [{ node_id: 'default', params: { max_current_a: 1.5 }, callback: 'current_limit', fallback: 'hold_position', timeout_sec: null, warn_frames: 3 }]
  },
  {
    name: 'voltage_limit', layer: 'L3', type: 'single',
    nodes: [{ node_id: 'default', params: { min_voltage_v: 10, max_voltage_v: 13 }, callback: 'voltage_limit', fallback: 'emergency_stop', timeout_sec: null, warn_frames: 3 }]
  },
  {
    name: 'host_health', layer: 'L3', type: 'single',
    nodes: [{ node_id: 'default', params: { max_cpu_percent: 99, max_memory_percent: 98, max_temperature_c: 95, max_gpu_percent: 99, max_gpu_temperature_c: 95 }, callback: 'host_health_limit', fallback: 'slow_down', timeout_sec: null, warn_frames: 3 }]
  },
]

// L2 task-level gripper sequence: only for real hardware with known task flow
const GRIPPER_SEQUENCE_BOUNDARY: BoundaryDef = {
  name: 'task_gripper_sequence', layer: 'L2', type: 'list',
  nodes: [
    { node_id: 'pick_left', params: { allowed_command: 'close', zone: LEFT_PICK_ZONE }, callback: 'task_gripper_command_guard', fallback: 'hold_position', timeout_sec: null },
    { node_id: 'transfer_left_to_right', params: { allowed_command: 'none' }, callback: 'task_gripper_command_guard', fallback: 'hold_position', timeout_sec: null },
    { node_id: 'place_right', params: { allowed_command: 'open', zone: RIGHT_PLACE_ZONE }, callback: 'task_gripper_command_guard', fallback: 'hold_position', timeout_sec: null },
  ],
}

const LAYER_ORDER: Record<string, number> = { L0: 0, L1: 1, L2: 2, L3: 3 }

function orderBoundaries(boundaries: BoundaryDef[]): BoundaryDef[] {
  return [...boundaries].sort((a, b) => (LAYER_ORDER[a.layer] ?? 99) - (LAYER_ORDER[b.layer] ?? 99))
}

const DEFAULT_BOUNDARIES: BoundaryDef[] = orderBoundaries([...BASE_BOUNDARIES, GRIPPER_SEQUENCE_BOUNDARY])
const REPLAY_BOUNDARIES: BoundaryDef[] = orderBoundaries([...BASE_BOUNDARIES, GRIPPER_SEQUENCE_BOUNDARY])

const DEFAULT_FALLBACKS: FallbackDef[] = [
  { name: 'emergency_stop', type: 'emergency_stop', severity: 100, requires_proposal: false, monitors_hardware: false, description: 'Immediate full stop. Highest severity.', params: {}, escalate_to: null },
  { name: 'hold_position', type: 'hold_position', severity: 80, requires_proposal: false, monitors_hardware: false, description: 'Hold current joint positions until trigger clears.', params: {}, escalate_to: null },
  { name: 'retreat', type: 'retreat', severity: 60, requires_proposal: false, monitors_hardware: false, description: 'Retract along the last safe trajectory segment.', params: { duration_seconds: 3.0, arrival_tol: 0.01 }, escalate_to: null },
  { name: 'slow_down', type: 'slow_down', severity: 40, requires_proposal: true, monitors_hardware: false, description: 'Scale action magnitude while monitoring trigger.', params: { scale: 0.5 }, escalate_to: null },
  { name: 'wait_and_retry', type: 'wait_and_retry', severity: 20, requires_proposal: false, monitors_hardware: false, description: 'Pause and re-check the trigger after a delay.', params: { wait_seconds: 1.0 }, escalate_to: null },
]

// QP fusion is now mandatory for all L1 boundaries — no opt-in needed.
// slack_weight is set per-boundary in DEFAULT_BOUNDARIES.

export const TEMPLATES: TemplatePreset[] = [
    {
    id: 'quick_start',
    label: 'Quick Start · Sim',
    description: 'Replay data through the safety pipeline.',
    badge: 'Demo',
    config: {
      hardware_preset: 'so101_follower', adapter: 'simulation',
      simulation_dataset_repo_id: 'MikeChenYZ/soarm-fmb-v2', simulation_episode: 0,
      policy: { type: 'act', pretrained_path: 'MikeChenYZ/act-soarm-fmb-v2', device: 'cpu' },
      joints: SO101_JOINTS, controlFrequencyHz: 30, enforcement_mode: 'monitor',
      fallbacks: DEFAULT_FALLBACKS,
      tasks: [{ id: 'demo', name: 'demo', description: 'Full demo', boundaries: REPLAY_BOUNDARIES.map(b => b.name) }],
      boundaries: REPLAY_BOUNDARIES,
      loopback: {
        backend: 'mcap', output_dir: './data/robot/sessions', window_sec: 10,
        rotate_mb: 500, rotate_minutes: 60, max_queue_depth: 64, capture_images_on_clamp: true,
      },
    },
  },
  {
    id: 'dataset_replay_check',
    label: 'Dataset Replay · Check',
    description: 'Replay validated actions to hardware with live cameras.',
    badge: 'Replay',
    config: {
      hardware_preset: 'so101_follower', adapter: 'lerobot', lerobot_port: '/dev/tty.usbmodem5AA90244141',
      lerobot_robot_type: 'so101_follower', lerobot_robot_id: 'my_awesome_follower_arm', lerobot_cameras: SO101_CAMERAS,
      lerobot_degrees_mode: true,
      simulation_dataset_repo_id: 'MikeChenYZ/soarm-fmb-v2', simulation_episode: 0,
      dataset_replay_to_hardware: true,
      dataset_image_namespace: 'replay',
      observation_channels: SO101_HEALTH_CHANNELS,
      policy: { type: 'noop', pretrained_path: '', device: 'cpu' },
      joints: SO101_JOINTS, controlFrequencyHz: 30, enforcement_mode: 'enforce',
      fallbacks: DEFAULT_FALLBACKS,
      tasks: [{ id: 'replay_check', name: 'replay_check', description: 'Guard-checked dataset replay to hardware',
        boundaries: REPLAY_BOUNDARIES.map(b => b.name) }],
      boundaries: REPLAY_BOUNDARIES,
      loopback: {
        backend: 'mcap', output_dir: './data/robot/sessions', window_sec: 10,
        rotate_mb: 500, rotate_minutes: 60, max_queue_depth: 64, capture_images_on_clamp: true,
      },
    },
  },
  {
    id: 'so101',
    label: 'SO-101 · ACT',
    description: 'ACT control with built-in safety guards.',
    badge: 'LeRobot',
    config: {
      hardware_preset: 'so101_follower', adapter: 'lerobot', lerobot_port: '/dev/tty.usbmodem5AA90244141',
      lerobot_robot_type: 'so101_follower', lerobot_robot_id: 'my_awesome_follower_arm', lerobot_cameras: SO101_CAMERAS,
      lerobot_degrees_mode: true,
      observation_channels: SO101_HEALTH_CHANNELS,
      policy: { type: 'act', pretrained_path: 'MikeChenYZ/act-soarm-fmb-v2', device: 'mps' },
      joints: SO101_JOINTS, controlFrequencyHz: 30, enforcement_mode: 'enforce',
      fallbacks: DEFAULT_FALLBACKS,
      tasks: [{ id: 'soarm101', name: 'soarm101', description: 'Safety-filtered motion',
        boundaries: DEFAULT_BOUNDARIES.map(b => b.name) }],
      boundaries: DEFAULT_BOUNDARIES,
      loopback: {
        backend: 'mcap', output_dir: './data/robot/sessions', window_sec: 10,
        rotate_mb: 500, rotate_minutes: 60, max_queue_depth: 64, capture_images_on_clamp: true,
      },
    },
  },
  {
    id: 'ros2_minimal',
    label: 'ROS2',
    description: 'ROS2 source and command adapter.',
    badge: 'ROS2',
    config: {
      hardware_preset: 'so101_follower', adapter: 'ros2',
      ros2JointTopic: '/joint_states', ros2CmdTopic: '/joint_commands',
      observation_channels: ['effort', 'wrench'],
      policy: { type: 'act', pretrained_path: '', device: 'cpu' },
      controlFrequencyHz: 30, enforcement_mode: 'monitor',
      fallbacks: DEFAULT_FALLBACKS,
      tasks: [{ id: 'default', name: 'default', description: 'Default task', boundaries: REPLAY_BOUNDARIES.map(b => b.name) }],
      boundaries: REPLAY_BOUNDARIES,
      loopback: {
        backend: 'mcap', output_dir: './data/robot/sessions', window_sec: 10,
        rotate_mb: 500, rotate_minutes: 60, max_queue_depth: 64, capture_images_on_clamp: true,
      },
    },
  },
]

function clonePresetConfig(config: Partial<DamConfig>): Partial<DamConfig> {
  // Presets intentionally contain configuration data only: no functions,
  // dates, or class instances. JSON cloning is stable in browsers and Jest.
  return JSON.parse(JSON.stringify(config)) as Partial<DamConfig>
}

export function defaultConfig(templateId = ''): DamConfig {
  const preset = TEMPLATES.find(t => t.id === templateId)
  const base: DamConfig = {
    templateId: '', // Always empty for stateless behavior
    hardware_preset: 'custom', input_space: 'joint', adapter: 'simulation', lerobot_port: '', lerobot_robot_type: 'so101_follower', lerobot_robot_id: '',
    lerobot_cameras: [], lerobot_calibration_path: '', lerobot_degrees_mode: true, observation_channels: [],
    ros2JointTopic: '/joint_states', ros2CmdTopic: '/joint_commands',
    policy: { type: 'noop', pretrained_path: '', device: 'cpu' },
    joints: SO101_JOINTS, controlFrequencyHz: 30, enforcement_mode: 'monitor',
    fallbacks: DEFAULT_FALLBACKS, guardsEnabled: {}, guardRouting: {}, tasks: [], boundaries: [],
  }
  if (!preset) return base
  // Each editor session owns its config; nested boundary edits must not
  // mutate the reusable preset shown elsewhere in the console.
  return { ...base, ...clonePresetConfig(preset.config), templateId: '' }
}

// ─────────────────────────────────────────────────────────────────────────────
// Recursive renderer
// ─────────────────────────────────────────────────────────────────────────────

function fmtValue(val: unknown): string {
  if (Array.isArray(val)) return `[${val.map(fmtValue).join(', ')}]`
  if (typeof val === 'number') return Number.isInteger(val) ? val.toString() : val.toFixed(4)
  if (typeof val === 'object' && val !== null) {
    return '\n' + Object.entries(val).map(([k, v]) => `  ${k}: ${fmtValue(v)}`).join('\n')
  }
  return String(val)
}

function fmtScalar(v: string | number | boolean | null | undefined): string | number | boolean {
  if (v == null) return 'null'
  if (typeof v === 'number') return Number.isInteger(v) ? v.toString() : v.toFixed(4)
  if (typeof v === 'boolean') return v
  return String(v)
}

function renderSection(node: YamlSection, cfg: DamConfig, indent = ''): string[] {
  switch (node.kind) {
    case 'blank': return ['']
    case 'scalar': {
      const v = node.value(cfg)
      if (v == null) return []
      return [`${indent}${node.key}: ${fmtScalar(v)}`]
    }
    case 'block': {
      if (node.when && !node.when(cfg)) return []
      const childLines = node.children.flatMap(c => renderSection(c, cfg, indent + '  '))
      if (childLines.length === 0) return []
      return [`${indent}${node.key}:`, ...childLines]
    }
    case 'list': {
      if (node.when && !node.when(cfg)) return []
      const groups = node.items(cfg)
      if (groups.length === 0) return [`${indent}${node.key}: []`]
      return [`${indent}${node.key}:`, ...groups.flatMap(itemLines =>
        itemLines.map((line, i) => `${indent}  ${i === 0 ? '- ' : '  '}${line}`)
      )]
    }
    case 'custom': {
      if (node.when && !node.when(cfg)) return []
      return node.lines(cfg, indent)
    }
  }
}

const blank: BlankNode = { kind: 'blank' }
const scalar = (key: string, value: ScalarNode['value']): ScalarNode => ({ kind: 'scalar', key, value })
const block = (key: string, children: YamlSection[], when?: BlockNode['when']): BlockNode => ({ kind: 'block', key, children, when })
const list = (key: string, items: ListNode['items'], when?: ListNode['when']): ListNode => ({ kind: 'list', key, items, when })
const custom = (lines: CustomNode['lines'], when?: CustomNode['when']): CustomNode => ({ kind: 'custom', lines, when })

// ─────────────────────────────────────────────────────────────────────────────
// Item renderers
// ─────────────────────────────────────────────────────────────────────────────

function boundaryLines(b: BoundaryDef): string[] {
  const lines: string[] = [`${b.name}:`, `  layer: ${b.layer}`, `  type: ${b.type}`, `  nodes:`]
  for (const node of b.nodes) {
    const isDefault = !node.node_id || node.node_id === 'default'
    lines.push(isDefault ? `    - callback: ${node.callback ?? 'null'}` : `    - node_id: ${node.node_id}`)
    if (!isDefault && node.callback) lines.push(`      callback: ${node.callback}`)
    if (node.timeout_sec != null) lines.push(`      timeout_sec: ${node.timeout_sec}`)
    if (node.warn_frames != null) lines.push(`      warn_frames: ${node.warn_frames}`)
    if (node.fallback) lines.push(`      fallback: ${node.fallback}`)
    if (node.params && Object.keys(node.params).length > 0) {
      lines.push('      params:')
      for (const [k, v] of Object.entries(node.params)) {
        if (v == null) continue
        lines.push(`        ${k}: ${fmtValue(v)}`)
      }
    }
  }
  return lines
}

function validBoundaries(cfg: DamConfig): BoundaryDef[] {
  return orderBoundaries(cfg.boundaries.filter(b => b.name.trim()))
}

function taskLines(t: TaskDef, boundaryOrder?: string[]): string[] {
  const lines: string[] = [`${t.name}:`]
  if (t.description) lines.push(`  description: "${t.description}"`)
  const chosen = new Set(t.boundaries)
  const refs = boundaryOrder ? boundaryOrder.filter(name => chosen.has(name)) : t.boundaries
  lines.push(refs.length > 0 ? `  boundaries: [${refs.join(', ')}]` : '  boundaries: []')
  return lines
}

const GUARD_LAYER: Record<string, string> = { ood: 'L0', motion: 'L1', execution: 'L2', hardware: 'L3' }

// Stackfile source-block name per adapter.  Used in three places (sources
// block key, channel ref, sink ref) — keep them in sync via this single map.
const MAIN_SOURCE_NAME: Record<DamConfig['adapter'], string> = {
  lerobot: 'arm',  // adapter value is still 'lerobot' in UI; emits `type: motor`
  ros2: 'ros2_source',
  simulation: 'main',
}

const GUARD_DEFAULT_ROUTING: Record<string, { phase?: number; always?: boolean; timeout_ms?: number }> = {
  ood:       { phase: 0, timeout_ms: 50 },
  motion:    { phase: 0, timeout_ms: 20 },
  execution: { phase: 1, timeout_ms: 20 },
  hardware:  { always: true, timeout_ms: 30 },
}

function guardLines(cfg: DamConfig): string[][] {
  const activeLayers = new Set(validBoundaries(cfg).map(boundary => boundary.layer))
  return (['ood', 'motion', 'execution', 'hardware'] as const).filter(gid =>
    activeLayers.has(GUARD_LAYER[gid])
  ).map(gid => {
    const layer = GUARD_LAYER[gid]
    const lines = [`${layer}: ${gid}`]
    if (cfg.guardsEnabled?.[gid] === false) lines.push('enabled: false')
    const routing = cfg.guardRouting?.[gid] ?? GUARD_DEFAULT_ROUTING[gid] ?? {}
    if (routing.always) lines.push(`always: ${routing.always}`)
    else if (routing.phase != null) lines.push(`phase: ${routing.phase}`)
    if (routing.timeout_ms != null) lines.push(`timeout_ms: ${routing.timeout_ms}`)
    return lines
  })
}

// ─────────────────────────────────────────────────────────────────────────────
// The schema tree
// ─────────────────────────────────────────────────────────────────────────────

const SCHEMA: YamlSection[] = [
  scalar('version', () => '"1"'), blank,
  block('hardware', [
    scalar('preset', cfg => cfg.hardware_preset),
    scalar('input_space', cfg => cfg.input_space ?? 'joint'),
    block('sources', [
      block('main', [
        scalar('type', () => 'dataset'),
        scalar('dataset_repo_id', cfg => cfg.simulation_dataset_repo_id ?? null),
        scalar('episode', cfg => cfg.simulation_episode ?? 0),
        scalar('degrees_mode', () => 'true'),
      ], cfg => cfg.adapter === 'simulation' && !!cfg.simulation_dataset_repo_id),
      block('replay', [
        scalar('type', () => 'dataset'),
        scalar('dataset_repo_id', cfg => cfg.simulation_dataset_repo_id ?? null),
        scalar('episode', cfg => cfg.simulation_episode ?? 0),
        scalar('degrees_mode', () => 'true'),
        scalar('image_namespace', cfg => cfg.dataset_image_namespace ?? 'replay'),
      ], cfg => cfg.adapter === 'lerobot' && !!cfg.dataset_replay_to_hardware && !!cfg.simulation_dataset_repo_id),
      block(MAIN_SOURCE_NAME.lerobot, [
        scalar('type', () => 'motor'), scalar('port', cfg => cfg.lerobot_port),
        scalar('robot_type', cfg => cfg.lerobot_robot_type || 'so101_follower'),
        scalar('id', cfg => cfg.lerobot_robot_id), scalar('calibration_path', cfg => cfg.lerobot_calibration_path || null),
        scalar('degrees_mode', cfg => cfg.lerobot_degrees_mode),
      ], cfg => cfg.adapter === 'lerobot'),
      // Cameras as peer-level opencv sources (flat, uniform with motor/ros2)
      custom((cfg, indent) => {
        return cfg.lerobot_cameras.flatMap(cam => {
          const idx = cam.source_type === 'udp' ? `"${cam.udp_url ?? ''}"` : String(cam.index ?? 0)
          return [
            `${indent}${cam.name}:`,
            `${indent}  type: opencv`,
            `${indent}  index_or_path: ${idx}`,
            `${indent}  width: ${cam.width}`,
            `${indent}  height: ${cam.height}`,
            `${indent}  fps: ${cam.fps}`,
          ]
        })
      }, cfg => cfg.adapter === 'lerobot' && cfg.lerobot_cameras.length > 0),
      block(MAIN_SOURCE_NAME.ros2, [
        scalar('type', () => 'ros2'),
        scalar('topic', cfg => cfg.ros2JointTopic),
      ], cfg => cfg.adapter === 'ros2'),
      // Peer-source observation channels (servo registers for lerobot, extra
      // topics for ROS2).  Parent ref points at whichever main source exists.
      // Optional `topic:` overrides the adapter's default topic per channel.
      // Skip blank / duplicate names — UI can hold transient empty rows.
      custom((cfg, indent) => {
        const parent = MAIN_SOURCE_NAME[cfg.adapter]
        const overrides = cfg.channel_topic_overrides ?? {}
        const seen = new Set<string>()
        return cfg.observation_channels.flatMap(ch => {
          if (!ch || seen.has(ch)) return []
          seen.add(ch)
          const lines = [`${indent}${ch}:`, `${indent}  type: ${ch}`, `${indent}  ref: ${parent}`]
          if (overrides[ch]) lines.push(`${indent}  topic: ${overrides[ch]}`)
          return lines
        })
      }, cfg => (cfg.adapter === 'lerobot' || cfg.adapter === 'ros2') && cfg.observation_channels.length > 0),
    ]),
    block('sinks', [
      block('main', [scalar('ref', () => `sources.${MAIN_SOURCE_NAME.simulation}`)], cfg => cfg.adapter === 'simulation' && !!cfg.simulation_dataset_repo_id),
      block('command', [scalar('ref', () => `sources.${MAIN_SOURCE_NAME.lerobot}`)], cfg => cfg.adapter === 'lerobot'),
      block('ros2_sink', [
        scalar('ref', () => `sources.${MAIN_SOURCE_NAME.ros2}`),
        scalar('topic', cfg => cfg.ros2CmdTopic),
      ], cfg => cfg.adapter === 'ros2'),
    ]),
  ]),
  blank,
  block('policy', [
    scalar('type', cfg => cfg.policy.type),
    scalar('input_space', cfg => cfg.input_space ?? 'joint'),
    scalar('pretrained_path', cfg => cfg.policy.pretrained_path),
    scalar('device', cfg => cfg.policy.device),
  ], cfg => !!cfg.policy.pretrained_path),
  blank,
  block('safety', [
    scalar('control_frequency_hz', cfg => cfg.controlFrequencyHz),
    scalar('no_task_behavior', () => 'emergency_stop'),
    scalar('enforcement_mode', cfg => cfg.enforcement_mode),
  ]),
  blank,
  custom((cfg, indent) => {
    const defs = cfg.fallbacks?.length ? cfg.fallbacks : DEFAULT_FALLBACKS
    return [
      `${indent}fallbacks:`,
      ...defs.flatMap(f => {
        const lines = [`${indent}  ${f.name}:`, `${indent}    type: ${f.type}`]
        if (f.severity != null) lines.push(`${indent}    severity: ${f.severity}`)
        if (f.requires_proposal) lines.push(`${indent}    requires_proposal: true`)
        if (f.monitors_hardware) lines.push(`${indent}    monitors_hardware: true`)
        if (f.escalate_to) lines.push(`${indent}    escalate_to: ${f.escalate_to}`)
        if (f.escalate_after_seconds != null) lines.push(`${indent}    escalate_after_seconds: ${f.escalate_after_seconds}`)
        const params = f.params ?? {}
        if (Object.keys(params).length > 0) {
          lines.push(`${indent}    params:`)
          Object.entries(params).forEach(([k, v]) => lines.push(`${indent}      ${k}: ${fmtValue(v)}`))
        }
        return lines
      }),
    ]
  }),
  blank,
  list('guards', guardLines),
  blank,
  custom((cfg, indent) => {
    const defs = validBoundaries(cfg)
    return !defs.length ? [`${indent}boundaries:`, `${indent}  {}`] : [`${indent}boundaries:`, ...defs.flatMap(b => boundaryLines(b).map(l => `${indent}  ${l}`))]
  }),
  blank,
  custom((cfg, indent) => {
    if (!cfg.tasks.length) return [`${indent}tasks:`, `${indent}  default:`, `${indent}    boundaries: []`]
    const names = validBoundaries(cfg).map(b => b.name)
    return [`${indent}tasks:`, ...cfg.tasks.flatMap(t => taskLines(t, names).map(l => `${indent}  ${l}`))]
  }),
  blank,
  block('loopback', [
    scalar('backend', cfg => cfg.loopback!.backend),
    scalar('output_dir', cfg => cfg.loopback!.output_dir),
    scalar('window_sec', cfg => cfg.loopback!.window_sec),
    scalar('rotate_mb', cfg => cfg.loopback!.rotate_mb),
    scalar('rotate_minutes', cfg => cfg.loopback!.rotate_minutes),
    scalar('max_queue_depth', cfg => cfg.loopback!.max_queue_depth),
    scalar('capture_images_on_clamp', cfg => cfg.loopback!.capture_images_on_clamp),
  ], cfg => !!cfg.loopback),
]

export function generateYaml(cfg: DamConfig): string {
  return SCHEMA.flatMap(section => renderSection(section, cfg)).join('\n') + '\n'
}

// ─────────────────────────────────────────────────────────────────────────────
// Parser
// ─────────────────────────────────────────────────────────────────────────────

export function parseConfigFromYaml(yaml: string): Partial<DamConfig> {
  const result: any = {}
  const getVal = (regex: RegExp) => {
    const m = yaml.match(regex)
    return m ? m[1].trim().replace(/^"(.*)"$/, '$1') : null
  }

  if (yaml.includes('type: motor') || yaml.includes('type: lerobot')) {
    result.adapter = 'lerobot'; result.lerobot_port = getVal(/port:\s*(.*)/);
    result.lerobot_robot_type = getVal(/robot_type:\s*(.*)/) || 'so101_follower';
    result.lerobot_robot_id = getVal(/(?<![_\w])id:\s*(.*)/); result.lerobot_calibration_path = getVal(/calibration_path:\s*(.*)/) || '';
    const motorBlock = /type:\s*(?:motor|lerobot)\s*\n([\s\S]*?)(?=\n\s{4}\w+:\s*\n|\n\s{2}sinks:)/.exec(yaml)?.[1] ?? ''
    const motorDegrees = /degrees_mode:\s*(true|false)/.exec(motorBlock)?.[1]
    result.lerobot_degrees_mode = (motorDegrees ?? 'true') === 'true'
    if (yaml.includes('type: dataset')) {
      result.dataset_replay_to_hardware = true
      result.simulation_dataset_repo_id = getVal(/dataset_repo_id:\s*(.*)/) ?? undefined
      const ep = getVal(/episode:\s*(\d+)/); if (ep != null) result.simulation_episode = Number(ep)
      result.dataset_image_namespace = getVal(/image_namespace:\s*(.*)/) ?? undefined
    }
  } else if (yaml.includes('type: ros2')) {
    result.adapter = 'ros2'
    // New canonical: `topic:` on the source/sink.  Old stackfiles used
    // `joint_topic:` / `cmd_topic:` — recover those as a fallback.
    result.ros2JointTopic = getVal(/(?:joint_topic|topic):\s*(.*)/)
    result.ros2CmdTopic = getVal(/cmd_topic:\s*(.*)/) ?? ''
  } else if (yaml.includes('type: dataset')) {
    result.adapter = 'simulation'; result.simulation_dataset_repo_id = getVal(/dataset_repo_id:\s*(.*)/) ?? undefined;
    const ep = getVal(/episode:\s*(\d+)/); if (ep != null) result.simulation_episode = Number(ep);
  }

  // hardware/policy input_space must match server-side; first hit wins.
  const inputSpace = getVal(/input_space:\s*(\w+)/)?.toLowerCase()
  if (inputSpace === 'joint' || inputSpace === 'ee') result.input_space = inputSpace

  const pType = getVal(/policy:\s*\n\s*type:\s*(.*)/)
  if (pType) {
    result.policy = {
      type: pType,
      pretrained_path: getVal(/pretrained_path:\s*(.*)/) || '',
      device: getVal(/device:\s*(.*)/) || 'cpu',
    }
  }

  const freq = getVal(/control_frequency_hz:\s*(\d+\.?\d*)/)
  if (freq) result.controlFrequencyHz = Number(freq)
  const mode = getVal(/enforcement_mode:\s*(.*)/)
  if (mode) result.enforcement_mode = mode as EnforcementMode

  const guardsEnabled: any = {}
  const guardRouting: any = {}
  for (const id of ['ood', 'motion', 'execution', 'hardware']) {
    const enMatch = new RegExp(`${id}:[\\s\\S]*?enabled:\\s*(true|false)`, 'i').exec(yaml)
    if (enMatch) guardsEnabled[id] = enMatch[1].toLowerCase() === 'true'
    const phaseMatch = new RegExp(`${id}:[\\s\\S]*?phase:\\s*(\\d+)`, 'i').exec(yaml)
    const alwaysMatch = new RegExp(`${id}:[\\s\\S]*?always:\\s*(true|false)`, 'i').exec(yaml)
    const timeoutMatch = new RegExp(`${id}:[\\s\\S]*?timeout_ms:\\s*(\\d+\\.?\\d*)`, 'i').exec(yaml)
    if (phaseMatch || alwaysMatch || timeoutMatch) {
      const entry: any = {}
      if (phaseMatch) entry.phase = Number(phaseMatch[1])
      if (alwaysMatch) entry.always = alwaysMatch[1].toLowerCase() === 'true'
      if (timeoutMatch) entry.timeout_ms = Number(timeoutMatch[1])
      guardRouting[id] = entry
    }
  }
  result.guardsEnabled = guardsEnabled
  if (Object.keys(guardRouting).length > 0) result.guardRouting = guardRouting

  const lines = yaml.split('\n'); let section: 'none' | 'boundaries' | 'tasks' | 'fallbacks' = 'none';
  let currentBoundary: any = null; let currentNode: any = null; let currentFallback: any = null
  let inFallbackParams = false
  const boundaries: any[] = []; const tasks: any[] = []; const fallbacks: any[] = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]; const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    if (line.startsWith('boundaries:')) { section = 'boundaries'; continue }
    if (line.startsWith('tasks:')) { section = 'tasks'; continue }
    if (line.startsWith('fallbacks:')) { section = 'fallbacks'; continue }
    if (line.startsWith('version:') || line.startsWith('safety:') || line.startsWith('guards:') || line.startsWith('hardware:') || line.startsWith('policy:') || line.startsWith('loopback:')) {
       section = 'none'; continue
    }

    if (section === 'fallbacks') {
      if (line.startsWith('  ') && !line.startsWith('    ')) {
        currentFallback = { name: trimmed.replaceAll(':', ''), type: '', params: {}, escalate_to: null, escalate_after_seconds: null }
        fallbacks.push(currentFallback)
        inFallbackParams = false
      } else if (currentFallback && line.startsWith('      ') && inFallbackParams) {
        const colonIdx = trimmed.indexOf(':'); if (colonIdx !== -1) {
          const key = trimmed.substring(0, colonIdx).trim(); const valRaw = trimmed.substring(colonIdx + 1).trim()
          if (key && valRaw) { try { currentFallback.params[key] = JSON.parse(valRaw) } catch { currentFallback.params[key] = valRaw } }
        }
      } else if (currentFallback && line.startsWith('    ')) {
        inFallbackParams = false
        if (trimmed === 'params:') { inFallbackParams = true }
        else if (trimmed.startsWith('type:')) currentFallback.type = trimmed.replaceAll('type:', '').trim()
        else if (trimmed.startsWith('severity:')) currentFallback.severity = Number(trimmed.replaceAll('severity:', '').trim())
        else if (trimmed.startsWith('requires_proposal:')) currentFallback.requires_proposal = trimmed.replaceAll('requires_proposal:', '').trim() === 'true'
        else if (trimmed.startsWith('monitors_hardware:')) currentFallback.monitors_hardware = trimmed.replaceAll('monitors_hardware:', '').trim() === 'true'
        else if (trimmed.startsWith('escalate_to:')) currentFallback.escalate_to = trimmed.replaceAll('escalate_to:', '').trim()
        else if (trimmed.startsWith('escalates_to:')) currentFallback.escalate_to = trimmed.replaceAll('escalates_to:', '').trim()
        else if (trimmed.startsWith('escalate_after_seconds:')) currentFallback.escalate_after_seconds = Number(trimmed.replaceAll('escalate_after_seconds:', '').trim())
      }
    } else if (section === 'boundaries') {
      if (line.startsWith('  ') && !line.startsWith('    ')) {
        currentBoundary = { name: trimmed.replaceAll(':', ''), layer: 'L1', type: 'single', nodes: [] }; boundaries.push(currentBoundary);
        currentNode = null  // reset — don't leak previous boundary's last node
      } else if (currentBoundary && line.startsWith('    ')) {
        // Container-level structural keys — must NOT fall through to node params
        if (trimmed.startsWith('layer:')) { currentBoundary.layer = trimmed.replaceAll('layer:', '').trim() }
        else if (trimmed.startsWith('type:')) { currentBoundary.type = trimmed.replaceAll('type:', '').trim() }
        else if (trimmed.startsWith('nodes:')) { /* skip header */ }
        else if (trimmed.startsWith('- node_id:') || trimmed.startsWith('- callback:')) {
          const isNodeId = trimmed.startsWith('- node_id:');
          currentNode = { node_id: isNodeId ? trimmed.replaceAll('- node_id:', '').trim() : 'default', params: {}, callback: isNodeId ? null : trimmed.replaceAll('- callback:', '').trim(), fallback: null, timeout_sec: null };
          currentBoundary.nodes.push(currentNode);
        } else if (currentNode) {
          if (trimmed.startsWith('callback:')) currentNode.callback = trimmed.replaceAll('callback:', '').trim()
          else if (trimmed.startsWith('fallback:')) currentNode.fallback = trimmed.replaceAll('fallback:', '').trim()
          else if (trimmed.startsWith('timeout_sec:')) currentNode.timeout_sec = Number(trimmed.replaceAll('timeout_sec:', '').trim())
          else if (trimmed.startsWith('warn_frames:')) currentNode.warn_frames = Number(trimmed.replaceAll('warn_frames:', '').trim())
          else if (trimmed === 'params:') { /* skip params header */ }
          else {
            const colonIdx = trimmed.indexOf(':'); if (colonIdx !== -1) {
              const key = trimmed.substring(0, colonIdx).trim(); const valRaw = trimmed.substring(colonIdx + 1).trim();
              if (key && valRaw) { try { currentNode.params[key] = JSON.parse(valRaw.replaceAll("'", '"')) } catch { currentNode.params[key] = valRaw } }
            }
          }
        }
      }
    } else if (section === 'tasks') {
      if (line.startsWith('  ') && !line.startsWith('    ')) {
        const name = trimmed.replaceAll(':', ''); const task: any = { id: name, name, description: '', boundaries: [] }; tasks.push(task);
        let j = i + 1; while (j < lines.length && lines[j].startsWith('    ')) {
          const tline = lines[j].trim();
          if (tline.startsWith('description:')) task.description = tline.replaceAll('description:', '').trim().replace(/^"(.*)"$/, '$1')
          if (tline.startsWith('boundaries:')) task.boundaries = tline.replaceAll('boundaries:', '').trim().replaceAll('[', '').replaceAll(']', '').split(',').map(s => s.trim()).filter(Boolean)
          j++
        }
        i = j - 1
      }
    }
  }
  if (boundaries.length > 0) result.boundaries = boundaries
  if (tasks.length > 0) result.tasks = tasks
  if (fallbacks.length > 0) result.fallbacks = fallbacks

  // Parse cameras from BOTH formats:
  // 1. Legacy nested: cameras: { top: { type: opencv, ... } }
  // 2. New flat peer sources: top:\n  type: opencv\n  index_or_path: 0\n  ...
  const cameras: CameraConfig[] = [];

  // Legacy nested format
  let inCameras = false;
  for (const line of lines) {
    if (line.includes('cameras:')) { inCameras = true; continue }
    if (inCameras && line.includes('{')) {
      const name = line.trim().split(':')[0];
      const match = /\{(.*)\}/.exec(line);
      if (match) {
        const params: any = {};
        match[1].split(',').forEach(p => {
          const pp = p.split(':').map(s => s.trim());
          if (pp.length >= 2) params[pp[0]] = pp[1];
        });
        cameras.push({ name, source_type: params.type, index: Number(params.index_or_path), udp_url: params.url, width: Number(params.width), height: Number(params.height), fps: Number(params.fps) });
      }
    } else if (inCameras && line.startsWith('    ') && !line.startsWith('      ')) { inCameras = false; }
  }

  // New flat peer-source opencv format
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(/^\s{4}(\w+):\s*$/)
    if (!m) continue
    const name = m[1]
    const next1 = lines[i + 1]?.trim() ?? ''
    if (next1 === 'type: opencv') {
      const cam: any = { name, source_type: 'opencv', width: 640, height: 480, fps: 30, index: 0 }
      for (let j = i + 2; j < lines.length && lines[j].startsWith('      '); j++) {
        const kv = lines[j].trim()
        if (kv.startsWith('index_or_path:')) cam.index = Number(kv.split(':')[1].trim())
        else if (kv.startsWith('width:')) cam.width = Number(kv.split(':')[1].trim())
        else if (kv.startsWith('height:')) cam.height = Number(kv.split(':')[1].trim())
        else if (kv.startsWith('fps:')) cam.fps = Number(kv.split(':')[1].trim())
      }
      // Avoid duplicates if legacy already caught this name
      if (!cameras.some(c => c.name === name)) cameras.push(cam)
    }
  }

  if (cameras.length > 0) result.lerobot_cameras = cameras

  // Channels are peer sources whose name == type and that carry `ref: <parent>`.
  // We don't hardcode the channel allowlist — that's the adapter's responsibility
  // server-side (validated against supported_channels()).
  const adapterTypes = new Set(['motor', 'lerobot', 'ros2', 'opencv', 'camera', 'usb', 'dataset', 'simulation', 'mock'])
  const obsChannels: string[] = []
  const channelTopics: Record<string, string> = {}
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(/^\s{4}(\w+):\s*$/)
    if (!m) continue
    const name = m[1]
    const next1 = lines[i + 1]?.trim() ?? ''
    const next2 = lines[i + 2]?.trim() ?? ''
    const next3 = lines[i + 3]?.trim() ?? ''
    const typeMatch = next1.match(/^type:\s+(\w+)/)
    if (typeMatch && typeMatch[1] === name && next2.startsWith('ref:') && !adapterTypes.has(name)) {
      obsChannels.push(name)
      const topicMatch = next3.match(/^topic:\s+(\S+)/)
      if (topicMatch) channelTopics[name] = topicMatch[1]
    }
  }
  if (obsChannels.length > 0) result.observation_channels = obsChannels
  if (Object.keys(channelTopics).length > 0) result.channel_topic_overrides = channelTopics

  if (yaml.includes('loopback:')) {
    result.loopback = {
      backend: (getVal(/backend:\s*(.*)/) || 'mcap') as any, output_dir: getVal(/output_dir:\s*(.*)/) || './data/robot/sessions',
      window_sec: Number(getVal(/window_sec:\s*(\d+\.?\d*)/) || 10),
      rotate_mb: Number(getVal(/rotate_mb:\s*(\d+\.?\d*)/) || 500), rotate_minutes: Number(getVal(/rotate_minutes:\s*(\d+\.?\d*)/) || 60),
      max_queue_depth: Number(getVal(/max_queue_depth:\s*(\d+)/) || 64), capture_images_on_clamp: getVal(/capture_images_on_clamp:\s*(true|false)/) === 'true',
    }
  }
  return result
}
