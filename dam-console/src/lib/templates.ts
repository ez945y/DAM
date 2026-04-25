// Schema-based recursive YAML generator & Parser
// ──────────────────────────────────────────────────────────────────────────

import type {
  EnforcementMode,
  JointDef,
  PolicyConfig,
  TaskDef,
  BoundaryDef,
  ConstraintNodeDef,
} from './types'

// ── Re-export types ──────────────────────────────────────────────────────────
export type { EnforcementMode, JointDef, PolicyConfig, TaskDef, BoundaryDef, ConstraintNodeDef }

export type LoopbackConfig = {
  backend: 'mcap' | 'pickle'
  output_dir: string
  window_sec: number
  pre_event_sec?: number
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
  ros2NodeName: string
  ros2JointTopic: string
  ros2CmdTopic: string
  ros2Namespace: string
  ros2WrenchTopic: string
  ros2Qos: 'reliable' | 'best_effort'
  policy: PolicyConfig
  joints: JointDef[]
  controlFrequencyHz: number
  enforcement_mode: EnforcementMode
  guardsEnabled: Partial<Record<'ood' | 'preflight' | 'motion' | 'execution' | 'hardware', boolean>>
  tasks: TaskDef[]
  boundaries: BoundaryDef[]
  loopback?: LoopbackConfig
  simulation_dataset_repo_id?: string
  simulation_episode?: number
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
  { name: 'gripper',       lower_rad:  0.0,    upper_rad:  1.7453 },
]

const SO101_CAMERAS: CameraConfig[] = [
  { name: 'top',   source_type: 'opencv', index: 0, width: 640, height: 480, fps: 30 },
  { name: 'wrist', source_type: 'opencv', index: 1, width: 640, height: 480, fps: 30 },
]

const DEFAULT_BOUNDARIES: BoundaryDef[] = [
  {
    name: 'ood_detector', layer: 'L0', type: 'single',
    nodes: [{ node_id: 'default', params: {}, callback: 'ood_detector', fallback: 'emergency_stop', timeout_sec: 1.0 }]
  },
  {
    name: 'bounds', layer: 'L2', type: 'single',
    nodes: [{ node_id: 'default', params: { bounds: [[-0.4, 0.4], [-0.4, 0.4], [0.02, 0.6]] }, callback: 'workspace', fallback: 'emergency_stop', timeout_sec: 1.0 }]
  },
  {
    name: 'joint_position_limits', layer: 'L2', type: 'single',
    nodes: [{ node_id: 'default', params: { upper: [1.8243, 1.7691, 1.6026, 1.8067, 3.0741, 1.7453], lower: [-1.8243, -1.7691, -1.6026, -1.8067, -3.0741, 0.0] }, callback: 'joint_position_limits', fallback: 'emergency_stop', timeout_sec: null }]
  },
  {
    name: 'joint_velocity_limit', layer: 'L2', type: 'single',
    nodes: [{ node_id: 'default', params: { max_velocities: [1.5, 1.5, 1.5, 1.5, 1.5, 1.5] }, callback: 'joint_velocity_limit', fallback: 'emergency_stop', timeout_sec: 1.0 }]
  },
  {
    name: 'hardware_watchdog', layer: 'L4', type: 'single',
    nodes: [{ node_id: 'default', params: { max_staleness_ms: 1000 }, callback: 'hardware_limit', fallback: 'emergency_stop', timeout_sec: 0.1 }]
  },
]

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
      joints: SO101_JOINTS, controlFrequencyHz: 15.0, enforcement_mode: 'monitor',
      tasks: [{ id: 'demo', name: 'demo', description: 'Full demo', boundaries: DEFAULT_BOUNDARIES.map(b => b.name) }],
      boundaries: DEFAULT_BOUNDARIES,
      loopback: {
        backend: 'mcap', output_dir: './data/robot/sessions', window_sec: 10.0, pre_event_sec: 10.0,
        rotate_mb: 500.0, rotate_minutes: 60.0, max_queue_depth: 64, capture_images_on_clamp: true,
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
      policy: { type: 'act', pretrained_path: 'MikeChenYZ/act-soarm-fmb-v2', device: 'mps' },
      joints: SO101_JOINTS, controlFrequencyHz: 15.0, enforcement_mode: 'enforce',
      tasks: [{ id: 'soarm101', name: 'soarm101', description: 'Default task', boundaries: ['bounds', 'joint_position_limits', 'joint_velocity_limit', 'hardware_watchdog'] }],
      boundaries: DEFAULT_BOUNDARIES,
      loopback: {
        backend: 'mcap', output_dir: './data/robot/sessions', window_sec: 10.0, pre_event_sec: 10.0,
        rotate_mb: 500.0, rotate_minutes: 60.0, max_queue_depth: 64, capture_images_on_clamp: true,
      },
    },
  },
  {
    id: 'so101_diffusion',
    label: 'SO-101 · Diffusion',
    description: 'SO-ARM101 with Diffusion Policy (DDIM scheduler, 15 steps).',
    badge: 'LeRobot',
    config: {
      hardware_preset: 'so101_follower', adapter: 'lerobot', lerobot_port: '/dev/tty.usbmodem5AA90244141',
      lerobot_robot_id: 'my_awesome_follower_arm', lerobot_cameras: SO101_CAMERAS,
      policy: { type: 'diffusion', pretrained_path: 'MikeChenYZ/dp-soarm-fmb', device: 'mps', noise_scheduler_type: 'DDIM', num_inference_steps: 15 },
      joints: SO101_JOINTS, controlFrequencyHz: 15.0, enforcement_mode: 'enforce',
      tasks: [{ id: 'soarm101', name: 'soarm101', description: 'Default task', boundaries: ['bounds', 'joint_position_limits', 'joint_velocity_limit', 'hardware_watchdog'] }],
      boundaries: DEFAULT_BOUNDARIES,
      loopback: {
        backend: 'mcap', output_dir: './data/robot/sessions', window_sec: 10.0, pre_event_sec: 10.0,
        rotate_mb: 500.0, rotate_minutes: 60.0, max_queue_depth: 64, capture_images_on_clamp: true,
      },
    },
  },
  {
    id: 'ros2_minimal',
    label: 'ROS2 Minimal',
    description: 'Minimal ROS2 source / sink adapter. Works with any ROS2-enabled robot.',
    badge: 'ROS2',
    config: {
      hardware_preset: 'generic_6dof', adapter: 'ros2', ros2NodeName: 'dam_node', ros2JointTopic: '/joint_states',
      ros2CmdTopic: '/joint_commands', ros2Namespace: '/dam', ros2WrenchTopic: '/wrench', ros2Qos: 'reliable',
      policy: { type: 'act', pretrained_path: '', device: 'cpu' },
      controlFrequencyHz: 15.0, enforcement_mode: 'monitor',
      tasks: [{ id: 'default', name: 'default', description: 'Default task', boundaries: [] }],
      boundaries: [],
      loopback: {
        backend: 'mcap', output_dir: './data/robot/sessions', window_sec: 10.0, pre_event_sec: 10.0,
        rotate_mb: 500.0, rotate_minutes: 60.0, max_queue_depth: 64, capture_images_on_clamp: true,
      },
    },
  },
]

export function defaultConfig(templateId = ''): DamConfig {
  const preset = TEMPLATES.find(t => t.id === templateId)
  const base: DamConfig = {
    templateId: '', // Always empty for stateless behavior
    hardware_preset: 'custom', adapter: 'simulation', lerobot_port: '', lerobot_robot_id: '',
    lerobot_cameras: [], lerobot_calibration_path: '', ros2NodeName: 'dam_node', ros2JointTopic: '/joint_states',
    ros2CmdTopic: '/joint_commands', ros2Namespace: '/dam', ros2WrenchTopic: '', ros2Qos: 'reliable',
    policy: { type: 'noop', pretrained_path: '', device: 'cpu' },
    joints: SO101_JOINTS, controlFrequencyHz: 10.0, enforcement_mode: 'monitor',
    guardsEnabled: {}, tasks: [], boundaries: [],
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

function cameraLines(cam: CameraConfig): string[] {
  const inner = `type: ${cam.source_type}, ${cam.source_type === 'udp' ? `url: "${cam.udp_url ?? ''}"` : `index_or_path: ${cam.index ?? 0}`}, width: ${cam.width}, height: ${cam.height}, fps: ${cam.fps}`;
  return [`${cam.name}: { ${inner} }`]
}

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

function taskLines(t: TaskDef): string[] {
  const lines: string[] = [`${t.name}:`]
  if (t.description) lines.push(`  description: "${t.description}"`)
  lines.push(t.boundaries.length > 0 ? `  boundaries: [${t.boundaries.join(', ')}]` : '  boundaries: []')
  return lines
}

const GUARD_LAYER: Record<string, string> = { ood: 'L0', preflight: 'L1', motion: 'L2', execution: 'L3', hardware: 'L4' }

function guardLines(cfg: DamConfig): string[][] {
  return (['ood', 'preflight', 'motion', 'execution', 'hardware'] as const).map(gid => {
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
    scalar('preset', cfg => cfg.adapter === 'simulation' ? 'simulation' : cfg.hardware_preset),
    block('sources', [
      block('main', [
        scalar('type', () => 'dataset'),
        scalar('dataset_repo_id', cfg => cfg.simulation_dataset_repo_id ?? null),
        scalar('episode', cfg => cfg.simulation_episode ?? 0),
        scalar('degrees_mode', () => 'true'),
      ], cfg => cfg.adapter === 'simulation' && !!cfg.simulation_dataset_repo_id),
      block('follower_arm', [
        scalar('type', () => 'lerobot'), scalar('port', cfg => cfg.lerobot_port),
        scalar('id', cfg => cfg.lerobot_robot_id), scalar('calibration_path', cfg => cfg.lerobot_calibration_path || null),
        custom((cfg, indent) => [`${indent}cameras:`, ...cfg.lerobot_cameras.flatMap(c => cameraLines(c).map(l => `${indent}  ${l}`))], cfg => cfg.lerobot_cameras.length > 0)
      ], cfg => cfg.adapter === 'lerobot'),
      block('ros2_source', [
        scalar('type', () => 'ros2'), scalar('node_name', cfg => cfg.ros2NodeName),
        scalar('joint_topic', cfg => cfg.ros2JointTopic), scalar('cmd_topic', cfg => cfg.ros2CmdTopic),
        scalar('namespace', cfg => cfg.ros2Namespace), scalar('wrench_topic', cfg => cfg.ros2WrenchTopic || '/wrench'),
        scalar('qos', cfg => cfg.ros2Qos),
      ], cfg => cfg.adapter === 'ros2'),
    ]),
    block('sinks', [
      block('main', [scalar('ref', () => 'sources.main')], cfg => cfg.adapter === 'simulation' && !!cfg.simulation_dataset_repo_id),
      block('follower_command', [scalar('ref', () => 'sources.follower_arm')], cfg => cfg.adapter === 'lerobot'),
      block('ros2_sink', [scalar('ref', () => 'sources.ros2_source')], cfg => cfg.adapter === 'ros2'),
    ]),
  ]),
  blank,
  block('policy', [
    scalar('type', cfg => cfg.policy.type), scalar('policy_id', cfg => cfg.policy.policy_id ?? null),
    scalar('pretrained_path', cfg => cfg.policy.pretrained_path), scalar('device', cfg => cfg.policy.device),
    scalar('noise_scheduler_type', cfg => cfg.policy.noise_scheduler_type ?? null),
    scalar('num_inference_steps', cfg => cfg.policy.num_inference_steps ?? null),
  ], cfg => !!cfg.policy.pretrained_path),
  blank,
  block('safety', [
    scalar('control_frequency_hz', cfg => cfg.controlFrequencyHz),
    scalar('no_task_behavior', () => 'emergency_stop'),
    scalar('enforcement_mode', cfg => cfg.enforcement_mode),
  ]),
  blank,
  list('guards', guardLines),
  blank,
  custom((cfg, indent) => !cfg.boundaries.length ? [`${indent}boundaries:`, `${indent}  {}`] : [`${indent}boundaries:`, ...cfg.boundaries.flatMap(b => boundaryLines(b).map(l => `${indent}  ${l}`))]),
  blank,
  custom((cfg, indent) => !cfg.tasks.length ? [`${indent}tasks:`, `${indent}  default:`, `${indent}    boundaries: []`] : [`${indent}tasks:`, ...cfg.tasks.flatMap(t => taskLines(t).map(l => `${indent}  ${l}`))]),
  blank,
  block('loopback', [
    scalar('backend', cfg => cfg.loopback!.backend),
    scalar('output_dir', cfg => cfg.loopback!.output_dir),
    scalar('window_sec', cfg => cfg.loopback!.window_sec),
    scalar('pre_event_sec', cfg => cfg.loopback!.pre_event_sec ?? 10.0),
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

  if (yaml.includes('type: lerobot')) {
    result.adapter = 'lerobot'; result.lerobot_port = getVal(/port:\s*(.*)/);
    result.lerobot_robot_id = getVal(/id:\s*(.*)/); result.lerobot_calibration_path = getVal(/calibration_path:\s*(.*)/) || '';
  } else if (yaml.includes('type: ros2')) {
    result.adapter = 'ros2'; result.ros2NodeName = getVal(/node_name:\s*(.*)/);
    result.ros2JointTopic = getVal(/joint_topic:\s*(.*)/); result.ros2CmdTopic = getVal(/cmd_topic:\s*(.*)/);
    result.ros2Namespace = getVal(/namespace:\s*(.*)/); result.ros2Qos = getVal(/qos:\s*(.*)/);
  } else if (/preset:\s*simulation/.test(yaml)) {
    result.adapter = 'simulation'; result.simulation_dataset_repo_id = getVal(/dataset_repo_id:\s*(.*)/) ?? undefined;
    const ep = getVal(/episode:\s*(\d+)/); if (ep != null) result.simulation_episode = Number(ep);
  }

  const pType = getVal(/policy:\s*\n\s*type:\s*(.*)/)
  if (pType) {
    result.policy = {
      type: pType, pretrained_path: getVal(/pretrained_path:\s*(.*)/) || '', device: getVal(/device:\s*(.*)/) || 'cpu',
      policy_id: getVal(/policy_id:\s*(.*)/), noise_scheduler_type: getVal(/noise_scheduler_type:\s*(.*)/),
      num_inference_steps: getVal(/num_inference_steps:\s*(\d+)/) ? Number(getVal(/num_inference_steps:\s*(\d+)/)) : undefined,
    }
  }

  const freq = getVal(/control_frequency_hz:\s*(\d+\.?\d*)/)
  if (freq) result.controlFrequencyHz = Number(freq)
  const mode = getVal(/enforcement_mode:\s*(.*)/)
  if (mode) result.enforcement_mode = mode as EnforcementMode

  const guardsEnabled: any = {}
  for (const id of ['ood', 'preflight', 'motion', 'execution', 'hardware']) {
    const enMatch = new RegExp(`${id}:[\\s\\S]*?enabled:\\s*(true|false)`, 'i').exec(yaml)
    if (enMatch) guardsEnabled[id] = enMatch[1].toLowerCase() === 'true'
  }
  result.guardsEnabled = guardsEnabled

  const lines = yaml.split('\n'); let section: 'none' | 'boundaries' | 'tasks' = 'none';
  let currentBoundary: any = null; let currentNode: any = null; const boundaries: any[] = []; const tasks: any[] = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]; const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    if (line.startsWith('boundaries:')) { section = 'boundaries'; continue }
    if (line.startsWith('tasks:')) { section = 'tasks'; continue }
    if (line.startsWith('version:') || line.startsWith('safety:') || line.startsWith('guards:') || line.startsWith('hardware:') || line.startsWith('policy:') || line.startsWith('loopback:')) {
       section = 'none'; continue
    }

    if (section === 'boundaries') {
      if (line.startsWith('  ') && !line.startsWith('    ')) {
        currentBoundary = { name: trimmed.replace(':', ''), layer: 'L2', type: 'single', nodes: [] }; boundaries.push(currentBoundary);
      } else if (currentBoundary && line.startsWith('    ')) {
        if (trimmed.startsWith('layer:')) currentBoundary.layer = trimmed.replace('layer:', '').trim()
        if (trimmed.startsWith('type:')) currentBoundary.type = trimmed.replace('type:', '').trim()
        if (trimmed.startsWith('- node_id:') || trimmed.startsWith('- callback:')) {
          const isNodeId = trimmed.startsWith('- node_id:');
          currentNode = { node_id: isNodeId ? trimmed.replace('- node_id:', '').trim() : 'default', params: {}, callback: isNodeId ? null : trimmed.replace('- callback:', '').trim(), fallback: 'emergency_stop', timeout_sec: 1.0 };
          currentBoundary.nodes.push(currentNode);
        } else if (currentNode) {
          if (trimmed.startsWith('callback:')) currentNode.callback = trimmed.replace('callback:', '').trim()
          else if (trimmed.startsWith('fallback:')) currentNode.fallback = trimmed.replace('fallback:', '').trim()
          else if (trimmed.startsWith('timeout_sec:')) currentNode.timeout_sec = Number(trimmed.replace('timeout_sec:', '').trim())
          else {
            const colonIdx = trimmed.indexOf(':'); if (colonIdx !== -1) {
              const key = trimmed.substring(0, colonIdx).trim(); const valRaw = trimmed.substring(colonIdx + 1).trim();
              if (key && valRaw) { try { currentNode.params[key] = JSON.parse(valRaw.replace(/'/g, '"')) } catch { currentNode.params[key] = valRaw } }
            }
          }
        }
      }
    } else if (section === 'tasks') {
      if (line.startsWith('  ') && !line.startsWith('    ')) {
        const name = trimmed.replace(':', ''); const task: any = { id: name, name, description: '', boundaries: [] }; tasks.push(task);
        let j = i + 1; while (j < lines.length && lines[j].startsWith('    ')) {
          const tline = lines[j].trim();
          if (tline.startsWith('description:')) task.description = tline.replace('description:', '').trim().replace(/^"(.*)"$/, '$1')
          if (tline.startsWith('boundaries:')) task.boundaries = tline.replace('boundaries:', '').trim().replace('[', '').replace(']', '').split(',').map(s => s.trim()).filter(Boolean)
          j++
        }
        i = j - 1
      }
    }
  }
  if (boundaries.length > 0) result.boundaries = boundaries
  if (tasks.length > 0) result.tasks = tasks

  const cameras: CameraConfig[] = []; let inCameras = false; let currentCam: any = null;
  for (const line of lines) {
    if (line.includes('cameras:')) { inCameras = true; continue }
    if (inCameras && line.includes('{')) {
      const name = line.trim().split(':')[0];
      const match = line.match(/\{(.*)\}/);
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
  if (cameras.length > 0) result.lerobot_cameras = cameras

  if (yaml.includes('loopback:')) {
    result.loopback = {
      backend: (getVal(/backend:\s*(.*)/) || 'mcap') as any, output_dir: getVal(/output_dir:\s*(.*)/) || './data/robot/sessions',
      window_sec: Number(getVal(/window_sec:\s*(\d+\.?\d*)/) || 10), pre_event_sec: Number(getVal(/pre_event_sec:\s*(\d+\.?\d*)/) || 10),
      rotate_mb: Number(getVal(/rotate_mb:\s*(\d+\.?\d*)/) || 500), rotate_minutes: Number(getVal(/rotate_minutes:\s*(\d+\.?\d*)/) || 60),
      max_queue_depth: Number(getVal(/max_queue_depth:\s*(\d+)/) || 64), capture_images_on_clamp: getVal(/capture_images_on_clamp:\s*(true|false)/) === 'true',
    }
  }
  return result
}
