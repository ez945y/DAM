import type {
  EnforcementMode,
  JointDef,
  PolicyConfig,
  TaskDef,
  BoundaryDef,
  ConstraintNodeDef,
} from './types'

// ── Re-export types that config page still uses ───────────────────────────────
export type { EnforcementMode, JointDef, PolicyConfig, TaskDef, BoundaryDef, ConstraintNodeDef }

// ── Loopback (MCAP) Config ────────────────────────────────────────────────────
export type LoopbackConfig = {
  backend: 'mcap' | 'pickle'
  output_dir: string
  window_sec: number        // ring-buffer depth for images (seconds)
  pre_event_sec?: number  // capture N seconds before event (0 = capture all)
  rotate_mb: number         // rotate file after this many MB
  rotate_minutes: number    // rotate file after this many minutes
  max_queue_depth: number   // drop cycles if queue exceeds this
  capture_images_on_clamp: boolean  // capture images on CLAMP events
}

// ── Camera type ───────────────────────────────────────────────────────────────
export type CameraConfig = {
  name: string
  source_type: 'opencv' | 'udp'
  index?: number         // used as index_or_path in generated YAML
  udp_url?: string
  width: number
  height: number
  fps: number
}

// ── DamConfig ────────────────────────────────────────────────────────────────

export interface DamConfig {
  templateId: string
  // Hardware
  hardware_preset: string  // 'so101_follower' | 'generic_6dof' | 'custom'
  adapter: 'lerobot' | 'ros2' | 'simulation'
  // LeRobot-specific
  lerobot_port: string
  lerobot_robot_id: string
  lerobot_cameras: CameraConfig[]
  // Path to calibration directory saved under the shared volume mount.
  // Written into hardware.sources.follower_arm.calibration_path.
  lerobot_calibration_path: string
  // ROS2-specific
  ros2NodeName: string
  ros2JointTopic: string
  ros2CmdTopic: string
  ros2Namespace: string
  ros2WrenchTopic: string
  ros2Qos: 'reliable' | 'best_effort'
  // Policy
  policy: PolicyConfig
  // Joint definitions (named)
  joints: JointDef[]
  // Safety
  controlFrequencyHz: number
  // Enforcement
  enforcement_mode: EnforcementMode
  // Per-guard enable/disable (undefined = enabled)
  guardsEnabled: Partial<Record<'ood' | 'preflight' | 'motion' | 'execution' | 'hardware', boolean>>
  // Tasks & Boundaries (for guard page)
  tasks: TaskDef[]
  boundaries: BoundaryDef[]
  // Loopback (MCAP recording)
  loopback?: LoopbackConfig
  // Simulation dataset replay (adapter === 'simulation' only)
  simulation_dataset_repo_id?: string
  simulation_episode?: number
}

// ── Template presets ───────────────────────────────────────────────────────
export interface TemplatePreset {
  id: string
  label: string
  description: string
  badge: string
  config: Partial<DamConfig>
}

// SO-101 joint limits (rad) — measured from hardware calibration
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

const DEFAULT_SO101_TASK: TaskDef = {
  id: 'soarm101',
  name: 'soarm101',
  description: 'Default pick-and-place task',
  boundaries: ['bounds', 'joint_position_limits', 'joint_velocity_limit', 'hardware_watchdog'],
}

const DEFAULT_BOUNDARIES: BoundaryDef[] = [
  {
    name: 'ood_detector',
    layer: 'L0',
    type: 'single',
    nodes: [
      {
        node_id: 'default',
        params: {},
        callback: 'ood_detector',
        fallback: 'emergency_stop',
        timeout_sec: 1.0,
      },
    ],
  },
  {
    name: 'bounds',
    layer: 'L2',
    type: 'single',
    nodes: [
      {
        node_id: 'default',
        params: {
          bounds: [[-0.4, 0.4], [-0.4, 0.4], [0.02, 0.6]],
        },
        callback: 'workspace',
        fallback: 'emergency_stop',
        timeout_sec: 1.0,
      },
    ],
  },
  {
    name: 'joint_position_limits',
    layer: 'L2',
    type: 'single',
    nodes: [
      {
        node_id: 'default',
        params: {
          upper: [1.8243, 1.7691, 1.6026, 1.8067, 3.0741, 1.7453],
          lower: [-1.8243, -1.7691, -1.6026, -1.8067, -3.0741, 0.0],
        },
        callback: 'joint_position_limits',
        fallback: 'emergency_stop',
        timeout_sec: null,
      },
    ],
  },
  {
    name: 'joint_velocity_limit',
    layer: 'L2',
    type: 'single',
    nodes: [
      {
        node_id: 'default',
        params: {
          max_velocities: [1.5, 1.5, 1.5, 1.5, 1.5, 1.5],
        },
        callback: 'joint_velocity_limit',
        fallback: 'emergency_stop',
        timeout_sec: 1.0,
      },
    ],
  },
  {
    name: 'hardware_watchdog',
    layer: 'L4',
    type: 'single',
    nodes: [
      {
        node_id: 'default',
        params: {
          max_staleness_ms: 1000,
        },
        callback: 'hardware_limit',
        fallback: 'emergency_stop',
        timeout_sec: 0.1,
      },
    ],
  },
]


const DEMO_TASK: TaskDef = {
  id: 'demo_full_setup',
  name: 'demo_full_setup',
  description: 'A full 5-layer safety guard configuration (L0-L4: Perception to Monitoring)',
  boundaries: DEFAULT_BOUNDARIES.map(b => b.name),
}

export const TEMPLATES: TemplatePreset[] = [
  {
    id: 'so101_act',
    label: 'SO-101 · ACT',
    description: 'SO-ARM101 follower arm with ACT policy. 6-DOF, pick-and-place task.',
    badge: 'LeRobot',
    config: {
      hardware_preset: 'so101_follower',
      adapter: 'lerobot',
      lerobot_port: '/dev/tty.usbmodem5AA90244141',
      lerobot_robot_id: 'my_awesome_follower_arm',
      lerobot_cameras: SO101_CAMERAS,
      lerobot_calibration_path: '',
      policy: {
        type: 'act',
        pretrained_path: 'MikeChenYZ/act-soarm-fmb-v2',
        device: 'mps',
      },
      joints: SO101_JOINTS,
      controlFrequencyHz: 15.0,
      enforcement_mode: 'enforce',
      tasks: [DEFAULT_SO101_TASK],
      boundaries: DEFAULT_BOUNDARIES,
    },
  },
  {
    id: 'so101_diffusion',
    label: 'SO-101 · Diffusion',
    description: 'SO-ARM101 with Diffusion Policy (DDIM scheduler, 15 steps). Requires CUDA or MPS.',
    badge: 'LeRobot',
    config: {
      hardware_preset: 'so101_follower',
      adapter: 'lerobot',
      lerobot_port: '/dev/tty.usbmodem5AA90244141',
      lerobot_robot_id: 'my_awesome_follower_arm',
      lerobot_cameras: SO101_CAMERAS,
      lerobot_calibration_path: '',
      policy: {
        type: 'diffusion',
        pretrained_path: 'MikeChenYZ/dp-soarm-fmb',
        device: 'mps',
        noise_scheduler_type: 'DDIM',
        num_inference_steps: 15,
      },
      joints: SO101_JOINTS,
      controlFrequencyHz: 15.0,
      enforcement_mode: 'enforce',
      tasks: [DEFAULT_SO101_TASK],
      boundaries: DEFAULT_BOUNDARIES,
    },
  },
  {
    id: 'ros2_minimal',
    label: 'ROS2 Minimal',
    description: 'Minimal ROS2 source / sink adapter. Works with any ROS2-enabled robot.',
    badge: 'ROS2',
    config: {
      hardware_preset: 'generic_6dof',
      adapter: 'ros2',
      lerobot_port: '',
      lerobot_robot_id: '',
      lerobot_cameras: [],
      ros2NodeName: 'dam_node',
      ros2JointTopic: '/joint_states',
      ros2CmdTopic: '/joint_commands',
      ros2Namespace: '/dam',
      ros2WrenchTopic: '/wrench',
      ros2Qos: 'reliable',
      policy: {
        type: 'act',
        pretrained_path: '',
        device: 'cpu',
      },
      joints: [
        { name: 'joint_1', lower_rad: -3.14, upper_rad: 3.14 },
        { name: 'joint_2', lower_rad: -3.14, upper_rad: 3.14 },
        { name: 'joint_3', lower_rad: -3.14, upper_rad: 3.14 },
        { name: 'joint_4', lower_rad: -3.14, upper_rad: 3.14 },
        { name: 'joint_5', lower_rad: -3.14, upper_rad: 3.14 },
        { name: 'joint_6', lower_rad: -3.14, upper_rad: 3.14 },
      ],
      controlFrequencyHz: 15.0,
      enforcement_mode: 'monitor',
      tasks: [{ id: 'default', name: 'default', description: 'Default task', boundaries: [] }],
      boundaries: [],
    },
  },
  {
    id: 'quick_start',
    label: 'Quick Start (Sim)',
    description: 'Full pipeline demo: replays real SO-ARM101 data with ACT policy through all 5 safety layers. No physical hardware needed.',
    badge: 'Demo',
    config: {
      hardware_preset: 'so101_follower',
      adapter: 'simulation',
      simulation_dataset_repo_id: 'MikeChenYZ/soarm-fmb-v2',
      simulation_episode: 0,
      policy: {
        type: 'act',
        pretrained_path: 'MikeChenYZ/act-soarm-fmb-v2',
        device: 'cpu',
      },
      joints: SO101_JOINTS,
      controlFrequencyHz: 15.0,
      enforcement_mode: 'monitor',
      tasks: [DEMO_TASK],
      boundaries: DEFAULT_BOUNDARIES,
      loopback: {
        backend: 'mcap',
        output_dir: './data/robot/sessions',
        window_sec: 10.0,
        pre_event_sec: 10.0,
        rotate_mb: 500.0,
        rotate_minutes: 60.0,
        max_queue_depth: 64,
        capture_images_on_clamp: true,
      },
    },
  },
]

// ── Default config ─────────────────────────────────────────────────────────

export function defaultConfig(templateId = 'quick_start'): DamConfig {
  const preset = TEMPLATES.find(t => t.id === templateId) ?? TEMPLATES[3]
  const base: DamConfig = {
    templateId,
    hardware_preset: 'custom',
    adapter: 'simulation',
    lerobot_port: '',
    lerobot_robot_id: '',
    lerobot_cameras: [],
    lerobot_calibration_path: '',
    ros2NodeName: 'dam_node',
    ros2JointTopic: '/joint_states',
    ros2CmdTopic: '/joint_commands',
    ros2Namespace: '/dam',
    ros2WrenchTopic: '',
    ros2Qos: 'reliable' as const,
    policy: {
      type: 'noop',
      pretrained_path: '',
      device: 'cpu',
    },
    joints: [
      { name: 'joint_1', lower_rad: -2.0, upper_rad: 2.0 },
      { name: 'joint_2', lower_rad: -2.0, upper_rad: 2.0 },
      { name: 'joint_3', lower_rad: -2.0, upper_rad: 2.0 },
      { name: 'joint_4', lower_rad: -2.0, upper_rad: 2.0 },
      { name: 'joint_5', lower_rad: -2.0, upper_rad: 2.0 },
      { name: 'joint_6', lower_rad: -2.0, upper_rad: 2.0 },
    ],
    controlFrequencyHz: 10.0,
    enforcement_mode: 'monitor',
    guardsEnabled: {},
    tasks: [{ id: 'default', name: 'default', description: 'Default simulation task', boundaries: ['bounds'] }],
    boundaries: [
      {
        name: 'bounds',
        layer: 'L2',
        type: 'single',
        nodes: [{
          node_id: 'default',
          params: {
            bounds: [[-0.4, 0.4], [-0.4, 0.4], [0.02, 0.6]],
          },
          callback: 'workspace',
          fallback: 'emergency_stop',
          timeout_sec: null,
        }],
      },
    ],
    // Default loopback config (MCAP recording)
    loopback: {
      backend: 'mcap',
      output_dir: './data/robot/sessions',
      window_sec: 10.0,
      pre_event_sec: 10.0,
      rotate_mb: 500.0,
      rotate_minutes: 60.0,
      max_queue_depth: 64,
      capture_images_on_clamp: true,
    },
  }

  return {
    ...base,
    ...preset.config,
    templateId,
    controlFrequencyHz: preset.config.controlFrequencyHz ?? base.controlFrequencyHz,
    lerobot_calibration_path: (preset.config as Partial<DamConfig>).lerobot_calibration_path ?? '',
    guardsEnabled:  (preset.config as Partial<DamConfig>).guardsEnabled  ?? {},
    loopback: (preset.config as Partial<DamConfig>).loopback ?? base.loopback,
    simulation_dataset_repo_id: (preset.config as Partial<DamConfig>).simulation_dataset_repo_id,
    simulation_episode: (preset.config as Partial<DamConfig>).simulation_episode,
  }
}

// ── YAML generator ────────────────────────────────────────────────────────

function fmtValue(val: any, indent = ''): string {
  if (Array.isArray(val)) {
    if (val.length > 0 && Array.isArray(val[0])) {
      // For matrices/nested arrays, use flow style but with better spacing
      return `[${val.map(v => fmtValue(v, indent)).join(', ')}]`
    }
    return `[${val.map(v => fmtValue(v, indent)).join(', ')}]`
  }
  if (typeof val === 'number') {
    return Number.isInteger(val) ? val.toString() : val.toFixed(4)
  }
  if (typeof val === 'object' && val !== null) {
    // For nested params, expand to multi-line
    const lines = []
    for (const [k, v] of Object.entries(val)) {
      lines.push(`${indent}  ${k}: ${fmtValue(v, indent + '  ')}`)
    }
    return '\n' + lines.join('\n')
  }
  return String(val)
}

export function generateYaml(cfg: DamConfig): string {
  const lines: string[] = []

  lines.push('version: "1"')
  lines.push('')

  // ── hardware ──────────────────────────────────────────────────────────────
  if (cfg.adapter === 'simulation') {
    lines.push('hardware:')
    lines.push('  preset: simulation')
    if (cfg.simulation_dataset_repo_id) {
      lines.push('  sources:')
      lines.push('    main:')
      lines.push('      type: dataset')
      lines.push(`      dataset_repo_id: ${cfg.simulation_dataset_repo_id}`)
      lines.push(`      episode: ${cfg.simulation_episode ?? 0}`)
      lines.push('      degrees_mode: true')
      lines.push('  sinks:')
      lines.push('    main:')
      lines.push('      ref: sources.main')
    }
    lines.push('')
  } else {
    lines.push('hardware:')
    lines.push(`  preset: ${cfg.hardware_preset}`)
    lines.push('  sources:')
  }

  if (cfg.adapter === 'lerobot') {
    lines.push('    follower_arm:')
    lines.push('      type: lerobot')
    lines.push(`      port: ${cfg.lerobot_port}`)
    lines.push(`      id: ${cfg.lerobot_robot_id}`)
    if (cfg.lerobot_calibration_path) {
      lines.push(`      calibration_path: ${cfg.lerobot_calibration_path}`)
    }
    // Cameras as first-class citizens
    if (cfg.lerobot_cameras.length > 0) {
      for (const cam of cfg.lerobot_cameras) {
        lines.push(`    ${cam.name}:`)
        lines.push(`      type: ${cam.source_type}`)
        if (cam.source_type === 'udp') {
          lines.push(`      url: "${cam.udp_url ?? ''}"`)
        } else {
          lines.push(`      index_or_path: ${cam.index ?? 0}`)
        }
        lines.push(`      width: ${cam.width}`)
        lines.push(`      height: ${cam.height}`)
        lines.push(`      fps: ${cam.fps}`)
      }
    }
    lines.push('  sinks:')
    lines.push('    follower_command:')
    lines.push('      ref: sources.follower_arm')
    lines.push('')
  } else if (cfg.adapter === 'ros2') {
    lines.push('    ros2_source:')
    lines.push('      type: ros2')
    lines.push(`      node_name: ${cfg.ros2NodeName}`)
    lines.push(`      joint_topic: ${cfg.ros2JointTopic}`)
    lines.push(`      cmd_topic: ${cfg.ros2CmdTopic}`)
    lines.push(`      namespace: ${cfg.ros2Namespace}`)
    lines.push(`      wrench_topic: ${cfg.ros2WrenchTopic || '/wrench'}`)
    lines.push(`      qos: ${cfg.ros2Qos}`)
    lines.push('  sinks:')
    lines.push('    ros2_sink:')
    lines.push('      ref: sources.ros2_source')
    lines.push('')
  }

  // ── policy ────────────────────────────────────────────────────────────────
  // Only emit policy section when a pretrained_path is actually provided
  if (cfg.policy.pretrained_path) {
    lines.push('policy:')
    lines.push(`  type: ${cfg.policy.type}`)
    if (cfg.policy.policy_id) {
      lines.push(`  policy_id: ${cfg.policy.policy_id}`)
    }
    lines.push(`  pretrained_path: ${cfg.policy.pretrained_path}`)
    lines.push(`  device: ${cfg.policy.device}`)
    // Diffusion-specific params
    if (cfg.policy.noise_scheduler_type) {
      lines.push(`  noise_scheduler_type: ${cfg.policy.noise_scheduler_type}`)
    }
    if (cfg.policy.num_inference_steps != null) {
      lines.push(`  num_inference_steps: ${cfg.policy.num_inference_steps}`)
    }
    lines.push('')
  }

  // ── safety ────────────────────────────────────────────────────────────────
  lines.push('safety:')
  lines.push(`  control_frequency_hz: ${cfg.controlFrequencyHz}`)
  lines.push('  no_task_behavior: emergency_stop')
  lines.push(`  enforcement_mode: ${cfg.enforcement_mode}`)
  lines.push('')

  // ── guards ────────────────────────────────────────────────────────────────
  lines.push('')
  lines.push('guards:')

  const GUARD_IDS = ['ood', 'preflight', 'motion', 'execution', 'hardware'] as const
  for (const gid of GUARD_IDS) {
    const layerMap: Record<string, string> = {
      ood: 'L0', preflight: 'L1', motion: 'L2', execution: 'L3', hardware: 'L4'
    }
    const layer = layerMap[gid] || 'L2'
    
    if (cfg.guardsEnabled?.[gid] === false) {
      lines.push(`  - ${layer}: ${gid}`)
      lines.push('    enabled: false')
    } else {
      lines.push(`  - ${layer}: ${gid}`)
    }
  }

  // ── boundaries ────────────────────────────────────────────────────────────
  lines.push('')
  lines.push('boundaries:')
  if (cfg.boundaries.length === 0) {
    lines.push('  {}')
  } else {
    for (const boundary of cfg.boundaries) {
      lines.push(`  ${boundary.name}:`)
      lines.push(`    layer: ${boundary.layer}`)
      lines.push(`    type: ${boundary.type}`)
      lines.push('    nodes:')
      for (const node of boundary.nodes) {
        const isDefaultId = !node.node_id || node.node_id === 'default'
        
        // Start the YAML list item with node_id if customized, otherwise start with callback
        if (!isDefaultId) {
          lines.push(`      - node_id: ${node.node_id}`)
          if (node.callback) lines.push(`        callback: ${node.callback}`)
        } else {
          lines.push(`      - callback: ${node.callback || 'null'}`)
        }

        if (node.timeout_sec !== null && node.timeout_sec !== undefined) {
          lines.push(`        timeout_sec: ${node.timeout_sec}`)
        }

        lines.push(`        fallback: ${node.fallback}`)

        if (node.params && Object.keys(node.params).length > 0) {
          lines.push('        params:')
          for (const [key, val] of Object.entries(node.params)) {
            if (val === null || val === undefined) continue
            lines.push(`          ${key}: ${fmtValue(val)}`)
          }
        }
      }
    }
  }

  // ── tasks (Guard Pipeline) ────────────────────────────────────────────────
  lines.push('')
  lines.push('tasks:')
  if (cfg.tasks.length === 0) {
    lines.push('  default:')
    lines.push('    boundaries: []')
  } else {
    for (const task of cfg.tasks) {
      lines.push(`  ${task.name}:`)
      if (task.description) {
        lines.push(`    description: "${task.description}"`)
      }
      if (task.boundaries.length > 0) {
        lines.push(`    boundaries: [${task.boundaries.join(', ')}]`)
      } else {
        lines.push('    boundaries: []')
      }
    }
  }

  // ── loopback (MCAP recording) ──────────────────────────────────────────────
  if (cfg.loopback) {
    lines.push('')
    lines.push('loopback:')
    lines.push(`  backend: ${cfg.loopback.backend}`)
    lines.push(`  output_dir: ${cfg.loopback.output_dir}`)
    lines.push(`  window_sec: ${cfg.loopback.window_sec}`)
    lines.push(`  pre_event_sec: ${cfg.loopback.pre_event_sec ?? 10}`)
    lines.push(`  rotate_mb: ${cfg.loopback.rotate_mb}`)
    lines.push(`  rotate_minutes: ${cfg.loopback.rotate_minutes}`)
    lines.push(`  max_queue_depth: ${cfg.loopback.max_queue_depth}`)
    lines.push(`  capture_images_on_clamp: ${cfg.loopback.capture_images_on_clamp}`)
  }

  return lines.join('\n') + '\n'
}

export function parseConfigFromYaml(yaml: string): Partial<DamConfig> {
  const result: any = {}

  // Basic regex helpers
  const getVal = (regex: RegExp) => {
    const m = yaml.match(regex)
    return m ? m[1].trim().replace(/^"(.*)"$/, '$1') : null
  }

  // 1. Hardware
  if (yaml.includes('type: lerobot')) {
    result.adapter = 'lerobot'
    result.lerobot_port = getVal(/port:\s*(.*)/)
    result.lerobot_robot_id = getVal(/id:\s*(.*)/)
    result.lerobot_calibration_path = getVal(/calibration_path:\s*(.*)/) || ''
  } else if (yaml.includes('type: ros2')) {
    result.adapter = 'ros2'
    result.ros2NodeName = getVal(/node_name:\s*(.*)/)
    result.ros2JointTopic = getVal(/joint_topic:\s*(.*)/)
    result.ros2CmdTopic = getVal(/cmd_topic:\s*(.*)/)
    result.ros2Namespace = getVal(/namespace:\s*(.*)/)
    result.ros2Qos = getVal(/qos:\s*(.*)/)
  } else if (/preset:\s*simulation/.test(yaml)) {
    result.adapter = 'simulation'
    result.simulation_dataset_repo_id = getVal(/dataset_repo_id:\s*(.*)/) ?? undefined
    const ep = getVal(/episode:\s*(\d+)/)
    if (ep != null) result.simulation_episode = Number(ep)
  }

  // 2. Policy
  const pType = getVal(/policy:\s*\n\s*type:\s*(.*)/)
  if (pType) {
    result.policy = {
      type: pType,
      pretrained_path: getVal(/pretrained_path:\s*(.*)/) || '',
      device: getVal(/device:\s*(.*)/) || 'cpu',
      policy_id: getVal(/policy_id:\s*(.*)/),
      noise_scheduler_type: getVal(/noise_scheduler_type:\s*(.*)/),
      num_inference_steps: getVal(/num_inference_steps:\s*(\d+)/) ? Number(getVal(/num_inference_steps:\s*(\d+)/)) : undefined,
    }
  }

  // 3. Safety
  const freq = getVal(/control_frequency_hz:\s*(\d+\.?\d*)/)
  if (freq) result.controlFrequencyHz = Number(freq)
  
  const mode = getVal(/enforcement_mode:\s*(.*)/)
  if (mode) result.enforcement_mode = mode as EnforcementMode

  // 4. Guards state
  const guardsEnabled: any = {}
  const ids = ['ood', 'preflight', 'motion', 'execution', 'hardware']
  for (const id of ids) {
    const enMatch = new RegExp(`${id}:[\\s\\S]*?enabled:\\s*(true|false)`, 'i').exec(yaml)
    if (enMatch) guardsEnabled[id] = enMatch[1].toLowerCase() === 'true'
  }
  result.guardsEnabled = guardsEnabled

  // 5. Advanced Sync: Boundaries & Tasks (Non-destructive but complete)
  // We use a simplified line-by-line parser as regex is too weak for nested blocks
  const lines = yaml.split('\n')
  let section: 'none' | 'boundaries' | 'tasks' = 'none'
  let currentBoundary: any = null
  let currentNode: any = null
  const boundaries: any[] = []
  const tasks: any[] = []

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith('#')) continue

    if (line.startsWith('boundaries:')) { section = 'boundaries'; continue }
    if (line.startsWith('tasks:')) { section = 'tasks'; continue }
    if (line.startsWith('version:') || line.startsWith('safety:') || line.startsWith('guards:') || line.startsWith('hardware:') || line.startsWith('policy:')) {
       section = 'none'; continue 
    }

    if (section === 'boundaries') {
      if (line.startsWith('  ') && !line.startsWith('    ')) {
        // New boundary
        const name = trimmed.replace(':', '')
        currentBoundary = { name, layer: 'L2', type: 'single', nodes: [] }
        boundaries.push(currentBoundary)
      } else if (currentBoundary && line.startsWith('    ')) {
        if (trimmed.startsWith('layer:')) currentBoundary.layer = trimmed.replace('layer:', '').trim()
        if (trimmed.startsWith('type:')) currentBoundary.type = trimmed.replace('type:', '').trim()
        if (trimmed.startsWith('- node_id:')) {
          currentNode = { node_id: trimmed.replace('- node_id:', '').trim(), params: {}, callback: null, fallback: 'emergency_stop', timeout_sec: 1.0 }
          currentBoundary.nodes.push(currentNode)
        } else if (currentNode) {
          if (trimmed.startsWith('callback:')) currentNode.callback = trimmed.replace('callback:', '').trim()
          else if (trimmed.startsWith('fallback:')) currentNode.fallback = trimmed.replace('fallback:', '').trim()
          else if (trimmed.startsWith('timeout_sec:')) currentNode.timeout_sec = Number(trimmed.replace('timeout_sec:', '').trim())
          else {
            // Generic param extractor
            const colonIdx = trimmed.indexOf(':')
            if (colonIdx !== -1) {
              const key = trimmed.substring(0, colonIdx).trim()
              const valRaw = trimmed.substring(colonIdx + 1).trim()
              if (key && valRaw) {
                try {
                  // Try parsing as JSON (for numbers, arrays, objects)
                  currentNode.params[key] = JSON.parse(valRaw.replace(/'/g, '"'))
                } catch {
                  // Fallback to plain string
                  currentNode.params[key] = valRaw
                }
              }
            }
          }
        }
      }
    } else if (section === 'tasks') {
      if (line.startsWith('  ') && !line.startsWith('    ')) {
        const name = trimmed.replace(':', '')
        const task: { id: string; name: string; description: string; boundaries: string[] } = { id: name, name, description: '', boundaries: [] }
        tasks.push(task)
        // Look ahead for boundaries
        let j = i + 1
        while (j < lines.length && lines[j].startsWith('    ')) {
          const tline = lines[j].trim()
          if (tline.startsWith('description:')) task.description = tline.replace('description:', '').trim().replace(/^"(.*)"$/, '$1')
          if (tline.startsWith('boundaries:')) {
            const braw = tline.replace('boundaries:', '').trim()
            task.boundaries = braw.replace('[', '').replace(']', '').split(',').map(s => s.trim()).filter(Boolean)
          }
          j++
        }
        i = j - 1
      }
    }
  }

  if (boundaries.length > 0) result.boundaries = boundaries
  if (tasks.length > 0) result.tasks = tasks

  // 4. Cameras (Expanded parser)
  const cameras: CameraConfig[] = []
  let inCameras = false
  let currentCam: any = null
  for (const line of lines) {
    const trimmed = line.trim()
    if (line.includes('cameras:')) { inCameras = true; continue }
    if (inCameras && line.startsWith('      ') && !line.startsWith('        ') && trimmed.endsWith(':')) {
      currentCam = { name: trimmed.replace(':', ''), width: 640, height: 480, fps: 30, source_type: 'opencv' }
      cameras.push(currentCam)
    } else if (currentCam && line.startsWith('          ')) {
      if (trimmed.startsWith('type:')) currentCam.source_type = trimmed.replace('type:', '').trim()
      if (trimmed.startsWith('index_or_path:')) currentCam.index = Number(trimmed.replace('index_or_path:', '').trim())
      if (trimmed.startsWith('url:')) currentCam.udp_url = trimmed.replace('url:', '').trim().replace(/"/g, '')
      if (trimmed.startsWith('width:')) currentCam.width = Number(trimmed.replace('width:', '').trim())
      if (trimmed.startsWith('height:')) currentCam.height = Number(trimmed.replace('height:', '').trim())
      if (trimmed.startsWith('fps:')) currentCam.fps = Number(trimmed.replace('fps:', '').trim())
    } else if (inCameras && line.startsWith('    ') && !line.startsWith('      ')) {
      inCameras = false
    }
  }
  if (cameras.length > 0) result.lerobot_cameras = cameras

  // 5. Loopback
  if (yaml.includes('loopback:')) {
    result.loopback = {
      backend: (getVal(/backend:\s*(.*)/) || 'mcap') as 'mcap' | 'pickle',
      output_dir: getVal(/output_dir:\s*(.*)/) || './data/robot/sessions',
      window_sec: Number(getVal(/window_sec:\s*(\d+\.?\d*)/) || 10),
      pre_event_sec: Number(getVal(/pre_event_sec:\s*(\d+\.?\d*)/) || 10),
      rotate_mb: Number(getVal(/rotate_mb:\s*(\d+\.?\d*)/) || 500),
      rotate_minutes: Number(getVal(/rotate_minutes:\s*(\d+\.?\d*)/) || 60),
      max_queue_depth: Number(getVal(/max_queue_depth:\s*(\d+)/) || 64),
      capture_images_on_clamp: getVal(/capture_images_on_clamp:\s*(true|false)/) === 'true',
    }
  }

  return result
}
