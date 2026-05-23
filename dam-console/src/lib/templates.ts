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
  adapter: 'lerobot' | 'ros2' | 'simulation'
  lerobot_port: string
  lerobot_robot_id: string
  lerobot_cameras: CameraConfig[]
  lerobot_calibration_path: string
  ros2JointTopic: string
  ros2CmdTopic: string
  policy: PolicyConfig
  joints: JointDef[]
  controlFrequencyHz: number
  enforcement_mode: EnforcementMode
  fallbacks: FallbackDef[]
  guardsEnabled: Partial<Record<'ood' | 'motion' | 'execution' | 'hardware', boolean>>
  tasks: TaskDef[]
  boundaries: BoundaryDef[]
  loopback?: LoopbackConfig
  simulation_dataset_repo_id?: string
  simulation_episode?: number
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
  { name: 'shoulder_pan',  lower_rad: -1.8243, upper_rad:  1.8243 },
  { name: 'shoulder_lift', lower_rad: -1.7691, upper_rad:  1.7691 },
  { name: 'elbow_flex',    lower_rad: -1.6026, upper_rad:  1.6026 },
  { name: 'wrist_flex',    lower_rad: -1.8067, upper_rad:  1.8067 },
  { name: 'wrist_roll',    lower_rad: -3.0741, upper_rad:  3.0741 },
  { name: 'gripper',       lower_rad:  0,    upper_rad:  1.7453 },
]

const SO101_CAMERAS: CameraConfig[] = [
  { name: 'top',   source_type: 'opencv', index: 0, width: 640, height: 480, fps: 30 },
  { name: 'wrist', source_type: 'opencv', index: 1, width: 640, height: 480, fps: 30 },
]

const SO101_HEALTH_CHANNELS = ['current', 'temperature', 'voltage']

const LEFT_PICK_ZONE = [[-0.175, -0.025], [-0.075, 0.075], [0.075, 0.225]]
const RIGHT_PLACE_ZONE = [[0.025, 0.175], [-0.075, 0.075], [0.075, 0.225]]

const DEFAULT_BOUNDARIES: BoundaryDef[] = [
  {
    name: 'workspace', layer: 'L1', type: 'single',
    nodes: [{ node_id: 'default', params: { bounds: [[-0.4, 0.4], [-0.4, 0.4], [0.02, 0.6]] }, callback: 'workspace', fallback: 'emergency_stop', timeout_sec: 1 }]
  },
  {
    name: 'joint_position_limits', layer: 'L1', type: 'single',
    nodes: [{ node_id: 'default', params: { upper: [1.8243, 1.7691, 1.6026, 1.8067, 3.0741, 1.7453], lower: [-1.8243, -1.7691, -1.6026, -1.8067, -3.0741, 0] }, callback: 'joint_position_limits', fallback: 'emergency_stop', timeout_sec: null }]
  },
  {
    name: 'joint_velocity_limit', layer: 'L1', type: 'single',
    nodes: [{ node_id: 'default', params: { max_velocities: [1.5, 1.5, 1.5, 1.5, 1.5, 1.5] }, callback: 'joint_velocity_limit', fallback: 'emergency_stop', timeout_sec: 1 }]
  },
  {
    name: 'task_gripper_sequence', layer: 'L2', type: 'list',
    nodes: [
      { node_id: 'pick_left', params: { allowed_command: 'close', zone: LEFT_PICK_ZONE }, callback: 'task_gripper_command_guard', fallback: 'hold_position', timeout_sec: null },
      { node_id: 'transfer_left_to_right', params: { allowed_command: 'none' }, callback: 'task_gripper_command_guard', fallback: 'hold_position', timeout_sec: null },
      { node_id: 'place_right', params: { allowed_command: 'open', zone: RIGHT_PLACE_ZONE }, callback: 'task_gripper_command_guard', fallback: 'hold_position', timeout_sec: null },
    ],
  },
  {
    name: 'hardware_watchdog', layer: 'L3', type: 'single',
    nodes: [{
      node_id: 'default',
      params: {
        max_staleness_ms: 1000,
        monitor_temperature: true,
        max_temperature_c: 80,
        monitor_current: true,
        max_current_a: 3,
        monitor_voltage: true,
        min_voltage_v: 10,
        max_voltage_v: 13,
        consecutive_fault_frames: 3,
        peak_action: 'warn',
      },
      callback: 'hardware_watchdog',
      fallback: 'emergency_stop',
      timeout_sec: 0.5,
    }]
  },
  {
    name: 'host_health', layer: 'L3', type: 'single',
    nodes: [{ node_id: 'default', params: { max_cpu_percent: 99, max_memory_percent: 98, max_temperature_c: 95, max_gpu_percent: 99, max_gpu_temperature_c: 95 }, callback: 'host_health_limit', fallback: 'emergency_stop', timeout_sec: 0.5 }]
  },
]

const DEFAULT_FALLBACKS: FallbackDef[] = [
  { name: 'emergency_stop', type: 'emergency_stop', params: {}, escalates_to: null },
  { name: 'hold_position', type: 'hold_position', params: {}, escalates_to: 'emergency_stop' },
]

// Helper: opt L1 motion boundaries into the ProxSuite QP fusion strategy.
// The runtime sees `qp_solver: proxsuite`, wires MotionGuard to the QP
// aggregator, and the callbacks pass solver-specific details through
// metadata. Each boundary stays decoupled in definition.
function withQpSolver(boundaries: BoundaryDef[]): BoundaryDef[] {
  const L1_MOTION = new Set(['joint_position_limits', 'joint_velocity_limit', 'workspace'])
  return boundaries.map(b =>
    !L1_MOTION.has(b.name) ? b : {
      ...b,
      nodes: b.nodes.map(n => ({
        ...n,
        params: { ...n.params, qp_solver: 'proxsuite', slack_weight: 1e8 },
      })),
    }
  )
}

export const TEMPLATES: TemplatePreset[] = [
    {
    id: 'quick_start',
    label: 'Quick Start · Sim',
    description: 'Full pipeline demo: replays real SO-ARM101 data with ACT policy.',
    badge: 'Demo',
    config: {
      hardware_preset: 'so101_follower', adapter: 'simulation',
      simulation_dataset_repo_id: 'MikeChenYZ/soarm-fmb-v2', simulation_episode: 0,
      policy: { type: 'act', pretrained_path: 'MikeChenYZ/act-soarm-fmb-v2', device: 'cpu' },
      joints: SO101_JOINTS, controlFrequencyHz: 30, enforcement_mode: 'monitor',
      tasks: [{ id: 'demo', name: 'demo', description: 'Full demo', boundaries: DEFAULT_BOUNDARIES.map(b => b.name) }],
      boundaries: DEFAULT_BOUNDARIES,
      loopback: {
        backend: 'mcap', output_dir: './data/robot/sessions', window_sec: 10,
        rotate_mb: 500, rotate_minutes: 60, max_queue_depth: 64, capture_images_on_clamp: true,
      },
    },
  },
  {
    id: 'so101_act',
    label: 'SO-101 · ACT',
    description: 'SO-ARM101 follower arm with ACT policy.',
    badge: 'LeRobot',
    config: {
      hardware_preset: 'so101_follower', adapter: 'lerobot', lerobot_port: '/dev/tty.usbmodem5AA90244141',
      lerobot_robot_id: 'my_awesome_follower_arm', lerobot_cameras: SO101_CAMERAS,
      observation_channels: SO101_HEALTH_CHANNELS,
      policy: { type: 'act', pretrained_path: 'MikeChenYZ/act-soarm-fmb-v2', device: 'mps' },
      joints: SO101_JOINTS, controlFrequencyHz: 30, enforcement_mode: 'enforce',
      tasks: [{ id: 'soarm101', name: 'soarm101', description: 'Default task', boundaries: ['workspace', 'joint_position_limits', 'joint_velocity_limit', 'task_gripper_sequence', 'hardware_watchdog', 'host_health'] }],
      boundaries: DEFAULT_BOUNDARIES,
      loopback: {
        backend: 'mcap', output_dir: './data/robot/sessions', window_sec: 10,
        rotate_mb: 500, rotate_minutes: 60, max_queue_depth: 64, capture_images_on_clamp: true,
      },
    },
  },
  {
    id: 'so101_act_qp',
    label: 'SO-101 · ACT + QP',
    description: 'SO-ARM101 ACT policy with ProxSuite QP safety filter at L1.',
    badge: 'LeRobot',
    config: {
      hardware_preset: 'so101_follower', adapter: 'lerobot', lerobot_port: '/dev/tty.usbmodem5AA90244141',
      lerobot_robot_id: 'my_awesome_follower_arm', lerobot_cameras: SO101_CAMERAS,
      observation_channels: SO101_HEALTH_CHANNELS,
      policy: { type: 'act', pretrained_path: 'MikeChenYZ/act-soarm-fmb-v2', device: 'mps' },
      joints: SO101_JOINTS, controlFrequencyHz: 30, enforcement_mode: 'enforce',
      tasks: [{ id: 'soarm101', name: 'soarm101', description: 'QP-protected motion',
        boundaries: ['workspace', 'joint_position_limits', 'joint_velocity_limit', 'task_gripper_sequence', 'hardware_watchdog', 'host_health'] }],
      boundaries: withQpSolver(DEFAULT_BOUNDARIES),
      loopback: {
        backend: 'mcap', output_dir: './data/robot/sessions', window_sec: 10,
        rotate_mb: 500, rotate_minutes: 60, max_queue_depth: 64, capture_images_on_clamp: true,
      },
    },
  },
  {
    id: 'ros2_minimal',
    label: 'ROS2',
    description: 'ROS2 source / sink adapter with observation channels. Works with any ROS2-enabled robot.',
    badge: 'ROS2',
    config: {
      hardware_preset: 'so101_follower', adapter: 'ros2',
      ros2JointTopic: '/joint_states', ros2CmdTopic: '/joint_commands',
      observation_channels: ['effort', 'wrench'],
      policy: { type: 'act', pretrained_path: '', device: 'cpu' },
      controlFrequencyHz: 30, enforcement_mode: 'monitor',
      tasks: [{ id: 'default', name: 'default', description: 'Default task', boundaries: [] }],
      boundaries: [],
      loopback: {
        backend: 'mcap', output_dir: './data/robot/sessions', window_sec: 10,
        rotate_mb: 500, rotate_minutes: 60, max_queue_depth: 64, capture_images_on_clamp: true,
      },
    },
  },
]

export function defaultConfig(templateId = ''): DamConfig {
  const preset = TEMPLATES.find(t => t.id === templateId)
  const base: DamConfig = {
    templateId: '', // Always empty for stateless behavior
    hardware_preset: 'custom', adapter: 'simulation', lerobot_port: '', lerobot_robot_id: '',
    lerobot_cameras: [], lerobot_calibration_path: '', observation_channels: [],
    ros2JointTopic: '/joint_states', ros2CmdTopic: '/joint_commands',
    policy: { type: 'noop', pretrained_path: '', device: 'cpu' },
    joints: SO101_JOINTS, controlFrequencyHz: 30, enforcement_mode: 'monitor',
    fallbacks: DEFAULT_FALLBACKS, guardsEnabled: {}, tasks: [], boundaries: [],
  }
  if (!preset) return base
  // Apply preset but keep identity anonymous (stateless)
  return { ...base, ...preset.config, templateId: '' }
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
    lines.push(`      fallback: ${node.fallback}`)
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
  return cfg.boundaries.filter(b => b.name.trim())
}

function taskLines(t: TaskDef, validNames?: Set<string>): string[] {
  const lines: string[] = [`${t.name}:`]
  if (t.description) lines.push(`  description: "${t.description}"`)
  const refs = validNames ? t.boundaries.filter(name => validNames.has(name)) : t.boundaries
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

function guardLines(cfg: DamConfig): string[][] {
  return (['ood', 'motion', 'execution', 'hardware'] as const).map(gid => {
    const layer = GUARD_LAYER[gid]
    return cfg.guardsEnabled?.[gid] === false ? [`${layer}: ${gid}`, 'enabled: false'] : [`${layer}: ${gid}`]
  })
}

// ─────────────────────────────────────────────────────────────────────────────
// The schema tree
// ─────────────────────────────────────────────────────────────────────────────

const SCHEMA: YamlSection[] = [
  scalar('version', () => '"1"'), blank,
  block('hardware', [
    scalar('preset', cfg => cfg.hardware_preset),
    block('sources', [
      block('main', [
        scalar('type', () => 'dataset'),
        scalar('dataset_repo_id', cfg => cfg.simulation_dataset_repo_id ?? null),
        scalar('episode', cfg => cfg.simulation_episode ?? 0),
        scalar('degrees_mode', () => 'true'),
      ], cfg => cfg.adapter === 'simulation' && !!cfg.simulation_dataset_repo_id),
      block(MAIN_SOURCE_NAME.lerobot, [
        scalar('type', () => 'motor'), scalar('port', cfg => cfg.lerobot_port),
        scalar('id', cfg => cfg.lerobot_robot_id), scalar('calibration_path', cfg => cfg.lerobot_calibration_path || null),
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
        if (f.escalates_to) lines.push(`${indent}    escalates_to: ${f.escalates_to}`)
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
    const names = new Set(validBoundaries(cfg).map(b => b.name))
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
    result.lerobot_robot_id = getVal(/id:\s*(.*)/); result.lerobot_calibration_path = getVal(/calibration_path:\s*(.*)/) || '';
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
  for (const id of ['ood', 'motion', 'execution', 'hardware']) {
    const enMatch = new RegExp(`${id}:[\\s\\S]*?enabled:\\s*(true|false)`, 'i').exec(yaml)
    if (enMatch) guardsEnabled[id] = enMatch[1].toLowerCase() === 'true'
  }
  result.guardsEnabled = guardsEnabled

  const lines = yaml.split('\n'); let section: 'none' | 'boundaries' | 'tasks' | 'fallbacks' = 'none';
  let currentBoundary: any = null; let currentNode: any = null; let currentFallback: any = null
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
        currentFallback = { name: trimmed.replaceAll(':', ''), type: '', params: {}, escalates_to: null }
        fallbacks.push(currentFallback)
      } else if (currentFallback && line.startsWith('    ')) {
        if (trimmed.startsWith('type:')) currentFallback.type = trimmed.replaceAll('type:', '').trim()
        else if (trimmed.startsWith('escalates_to:')) currentFallback.escalates_to = trimmed.replaceAll('escalates_to:', '').trim()
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
          currentNode = { node_id: isNodeId ? trimmed.replaceAll('- node_id:', '').trim() : 'default', params: {}, callback: isNodeId ? null : trimmed.replaceAll('- callback:', '').trim(), fallback: 'emergency_stop', timeout_sec: 1 };
          currentBoundary.nodes.push(currentNode);
        } else if (currentNode) {
          if (trimmed.startsWith('callback:')) currentNode.callback = trimmed.replaceAll('callback:', '').trim()
          else if (trimmed.startsWith('fallback:')) currentNode.fallback = trimmed.replaceAll('fallback:', '').trim()
          else if (trimmed.startsWith('timeout_sec:')) currentNode.timeout_sec = Number(trimmed.replaceAll('timeout_sec:', '').trim())
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
