import {
  TEMPLATES,
  defaultConfig,
  generateYaml,
  parseConfigFromYaml,
} from '@/lib/templates'

describe('TEMPLATES', () => {
  it('has 4 presets', () => {
    expect(TEMPLATES).toHaveLength(4)
  })

  it('every template has required fields', () => {
    for (const t of TEMPLATES) {
      expect(t.id).toBeTruthy()
      expect(t.label).toBeTruthy()
      expect(t.description).toBeTruthy()
      expect(t.badge).toBeTruthy()
    }
  })

  it('every template includes the shared L1 and L3 hardware monitors', () => {
    for (const t of TEMPLATES) {
      const cfg = defaultConfig(t.id)
      expect(cfg.tasks).toHaveLength(1)
      const layers = new Set(cfg.boundaries.map(b => b.layer))
      expect(layers).toContain('L1')
      expect(layers).toContain('L3')
      for (const boundary of ['hardware_watchdog', 'temperature_limit', 'current_limit', 'voltage_limit']) {
        expect(cfg.boundaries.map(b => b.name)).toContain(boundary)
        expect(cfg.tasks[0].boundaries).toContain(boundary)
      }
    }
  })

  it('uses multi-cycle L3 reactions and a 12 V-compatible supply band', () => {
    for (const t of TEMPLATES) {
      const cfg = defaultConfig(t.id)
      const byName = Object.fromEntries(cfg.boundaries.map(b => [b.name, b]))
      for (const name of ['hardware_watchdog', 'temperature_limit', 'current_limit', 'voltage_limit']) {
        expect(byName[name].nodes[0].warn_frames).toBeGreaterThan(1)
      }
      expect(byName.voltage_limit.nodes[0].params).toMatchObject({ min_voltage_v: 10, max_voltage_v: 13 })
      expect(byName.hardware_watchdog.nodes[0].timeout_sec).toBeNull()
      expect(byName.host_health.nodes[0].timeout_sec).toBeNull()
      expect(byName.host_health.nodes[0].fallback).toBe('slow_down')
    }
    expect(defaultConfig('so101').boundaries.map(b => b.name)).toContain('task_gripper_sequence')
    expect(defaultConfig('dataset_replay_check').boundaries.map(b => b.name)).not.toContain('task_joint_speed_limit')
  })
})

describe('defaultConfig', () => {
  it('returns a valid config for so101', () => {
    const cfg = defaultConfig('so101')
    expect(cfg.joints).toHaveLength(6)
    expect(cfg.adapter).toBe('lerobot')
    expect(cfg.policy.type).toBe('act')
    expect(cfg.hardware_preset).toBe('so101_follower')
    expect(cfg.enforcement_mode).toBe('enforce')
  })

  it('SO-101 ACT uses correct pretrained_path', () => {
    const cfg = defaultConfig('so101')
    expect(cfg.policy.pretrained_path).toBe('MikeChenYZ/act-soarm-fmb-v2')
    expect(cfg.policy.device).toBe('mps')
  })

  it('SO-101 joints have correct names', () => {
    const cfg = defaultConfig('so101')
    const names = cfg.joints.map(j => j.name)
    expect(names).toContain('shoulder_pan')
    expect(names).toContain('gripper')
  })

  it('SO-101 joints carry only names (limits live on boundaries)', () => {
    const cfg = defaultConfig('so101')
    const pan = cfg.joints.find(j => j.name === 'shoulder_pan')!
    const grip = cfg.joints.find(j => j.name === 'gripper')!
    expect(pan).toEqual({ name: 'shoulder_pan' })
    expect(grip).toEqual({ name: 'gripper' })
    // Position limits are on the joint_position_limits boundary, not on joints
    const posLimits = cfg.boundaries.find(b => b.name === 'joint_position_limits')!
    expect(posLimits.nodes[0].params.upper).toBeDefined()
    expect(posLimits.nodes[0].params.lower).toBeDefined()
  })

  it('SO-101 robot_id matches lerobot-record default', () => {
    const cfg = defaultConfig('so101')
    expect(cfg.lerobot_robot_type).toBe('so101_follower')
    expect(cfg.lerobot_robot_id).toBe('my_awesome_follower_arm')
  })

  it('SO-101 cameras use index_or_path convention', () => {
    const cfg = defaultConfig('so101')
    expect(cfg.lerobot_cameras).toHaveLength(2)
    expect(cfg.lerobot_cameras[0].name).toBe('top')
    expect(cfg.lerobot_cameras[1].name).toBe('wrist')
  })

  it('lerobot_calibration_path defaults to empty string', () => {
    const cfg = defaultConfig('so101')
    expect(cfg.lerobot_calibration_path).toBe('')
  })

  it('returns a simulation config for quick_start template', () => {
    const cfg = defaultConfig('quick_start')
    expect(cfg.adapter).toBe('simulation')
    expect(cfg.enforcement_mode).toBe('monitor')
    expect(cfg.tasks[0].boundaries).not.toContain('task_joint_speed_limit')
    expect(cfg.tasks[0].boundaries).toContain('workspace')
    expect(cfg.tasks[0].boundaries).toContain('hardware_watchdog')
    expect(cfg.tasks[0].boundaries).toContain('voltage_limit')
    expect(cfg.tasks[0].boundaries).toContain('host_health')
  })

  it('falls back to simulation for unknown template id', () => {
    const cfg = defaultConfig('nonexistent')
    expect(cfg.adapter).toBe('simulation')
  })

  it('ros2 config has correct fields', () => {
    const cfg = defaultConfig('ros2_minimal')
    expect(cfg.adapter).toBe('ros2')
    expect(cfg.ros2JointTopic).toBe('/joint_states')
    expect(cfg.controlFrequencyHz).toBe(30)
  })

  it('guardsEnabled defaults to empty object', () => {
    const cfg = defaultConfig('quick_start')
    expect(cfg.guardsEnabled).toBeDefined()
  })

  it('returns independent nested config objects for editor changes', () => {
    const first = defaultConfig('so101')
    first.boundaries[0].name = 'edited_locally'
    expect(defaultConfig('so101').boundaries[0].name).not.toBe('edited_locally')
  })
})

describe('generateYaml', () => {
  it('produces valid YAML string with required sections', () => {
    const cfg = defaultConfig('so101')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('version: "1"')
    expect(yaml).toContain('guards:')
    expect(yaml).toContain('safety:')
    expect(yaml).toContain('boundaries:')
    expect(yaml).toContain('tasks:')
  })

  it('includes hardware section for lerobot', () => {
    const cfg = defaultConfig('so101')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('hardware:')
    expect(yaml).toContain('so101_follower')
  })

  it('includes policy section for non-noop', () => {
    const cfg = defaultConfig('so101')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('policy:')
    expect(yaml).toContain('type: act')
  })

  it('quick_start emits the real hardware preset and dataset interface', () => {
    const cfg = defaultConfig('quick_start')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('hardware:')
    expect(yaml).toContain('preset: so101_follower')
    expect(yaml).toContain('interfaces:')
    expect(yaml).not.toContain('sources:')
    expect(yaml).not.toContain('sinks:')
    expect(yaml).toContain('type: dataset')
    expect(yaml).toContain('capabilities: [observe_joints]')
    expect(yaml).toContain('MikeChenYZ/soarm-fmb-v2')
  })

  it('dataset replay check emits dataset, hardware, and camera interfaces', () => {
    const cfg = defaultConfig('dataset_replay_check')
    const yaml = generateYaml(cfg)
    expect(yaml).toMatch(/replay:\s*\n\s*type: dataset\s*\n\s*capabilities: \[observe_joints\]/)
    expect(yaml).toMatch(/replay:[\s\S]*?image_namespace: replay/)
    expect(yaml).toMatch(/arm:\s*\n\s*type: motor\s*\n\s*capabilities: \[observe_joints, command_joints\]/)
    expect(yaml).toMatch(/top:\s*\n\s*type: opencv\s*\n\s*capabilities: \[image\]/)
    expect(yaml).toMatch(/wrist:\s*\n\s*type: opencv\s*\n\s*capabilities: \[image\]/)

    const parsed = parseConfigFromYaml(yaml)
    expect(parsed.dataset_replay_to_hardware).toBe(true)
    expect(parsed.simulation_dataset_repo_id).toBe('MikeChenYZ/soarm-fmb-v2')
    expect(parsed.dataset_image_namespace).toBe('replay')
    expect(parsed.lerobot_cameras).toHaveLength(2)

    const mixedUnits = yaml.replace(
      /(\n\s+arm:\s*\n\s+type: motor[\s\S]*?degrees_mode:) true/,
      '$1 false',
    )
    expect(parseConfigFromYaml(mixedUnits).lerobot_degrees_mode).toBe(false)
  })

  it('emits boundaries and task references in L0 through L3 order', () => {
    const cfg = defaultConfig('so101')
    cfg.boundaries = [...cfg.boundaries, {
      name: 'ood_detector',
      layer: 'L0',
      type: 'single',
      nodes: [{ node_id: 'default', callback: 'ood_detector', params: {} }],
    }]
    cfg.tasks = [{
      ...cfg.tasks[0],
      boundaries: [...cfg.tasks[0].boundaries, 'ood_detector'],
    }]
    const yaml = generateYaml(cfg)
    const positions = ['ood_detector:', 'workspace:', 'task_gripper_sequence:', 'hardware_watchdog:']
      .map(name => yaml.indexOf(`  ${name}`))
    expect(positions).toEqual([...positions].sort((a, b) => a - b))
    expect(yaml).toContain('boundaries: [ood_detector, workspace')
  })

  it('roundtrips warn_frames and emits the 12 V supply band', () => {
    const yaml = generateYaml(defaultConfig('dataset_replay_check'))
    expect(yaml).not.toContain('task_joint_speed_limit:')
    const l3Section = yaml.slice(yaml.indexOf('  hardware_watchdog:'), yaml.indexOf('\ntasks:'))
    expect(l3Section).not.toContain('timeout_sec:')
    expect(yaml).toMatch(/voltage_limit:[\s\S]*?warn_frames: 3[\s\S]*?min_voltage_v: 10[\s\S]*?max_voltage_v: 13/)
    const parsed = parseConfigFromYaml(yaml)
    const voltage = parsed.boundaries!.find(b => b.name === 'voltage_limit')!
    expect(voltage.nodes[0].warn_frames).toBe(3)
  })

  it('omits USB section entirely (USB config removed from stackfile)', () => {
    const yaml = generateYaml(defaultConfig('so101'))
    expect(yaml).not.toContain('usb_devices:')
  })

  it('guards section contains list of active guards — no guard-specific params', () => {
    const cfg = defaultConfig('so101')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('guards:')
    expect(yaml).toContain('  - L1: motion')
    expect(yaml).toContain('  - L2: execution')
    expect(yaml).toContain('  - L3: hardware')
    expect(yaml).not.toContain('  - L0: ood')
    expect(yaml).not.toContain('upper_limits:')
    expect(yaml).not.toContain('lower_limits:')
    expect(yaml).not.toContain('ood_model_path:')
    expect(yaml).not.toContain('nn_threshold:')
  })

  it('includes fallbacks block with severity and no separate type', () => {
    const cfg = defaultConfig('quick_start')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('fallbacks:')
    expect(yaml).toContain('emergency_stop:')
    expect(yaml).not.toContain('type: emergency_stop')
    expect(yaml).toContain('severity: 100')
    expect(yaml).toContain('hold_position:')
    expect(yaml).toContain('severity: 80')
    expect(yaml).toContain('slow_down:')
    expect(yaml).toContain('requires_proposal: true')
  })

  it('roundtrips fallbacks through generate → parse', () => {
    const cfg = defaultConfig('so101')
    const yaml = generateYaml(cfg)
    const parsed = parseConfigFromYaml(yaml)
    expect(parsed.fallbacks).toHaveLength(5)
    const estop = parsed.fallbacks!.find((f: any) => f.name === 'emergency_stop')
    expect(estop).toBeDefined()
    expect(estop!.severity).toBe(100)
    const slow = parsed.fallbacks!.find((f: any) => f.name === 'slow_down')
    expect(slow!.requires_proposal).toBe(true)
  })

  it('emits only guard layers backed by template boundaries', () => {
    const cfg = defaultConfig('so101')
    const yaml = generateYaml(cfg)
    expect(yaml).not.toContain('- L0: ood')
    expect(yaml).toContain('- L1: motion')
    expect(yaml).toContain('- L2: execution')
    expect(yaml).toContain('- L3: hardware')
  })

  it('joint limits appear in boundaries with calibrated values', () => {
    const cfg = defaultConfig('so101')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('joint_position_limits:')
    expect(yaml).toContain('upper:')
    expect(yaml).toContain('lower:')
    expect(yaml).toContain('use_degrees: true')
    // shoulder_pan upper limit, emitted in degrees to match the SO-101 preset mode
    expect(yaml).toContain('104.5247')
    // gripper lower limit is 0
    expect(yaml).toContain('0')
  })

  it('emits and parses the robot degrees_mode used by the SO-101 template', () => {
    const cfg = defaultConfig('so101')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('degrees_mode: true')
    const parsed = parseConfigFromYaml(yaml)
    expect(parsed.lerobot_degrees_mode).toBe(true)
  })

  it('includes workspace bounds in boundaries', () => {
    const cfg = defaultConfig('so101')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('workspace:')
    expect(yaml).toContain('bounds:')
    expect(yaml).toContain('hardware_watchdog:')
  })

  it('includes the left-to-right task gripper sequence', () => {
    const cfg = defaultConfig('so101')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('task_gripper_sequence:')
    expect(yaml).toContain('type: list')
    expect(yaml).toContain('node_id: pick_left')
    expect(yaml).toContain('allowed_command: close')
    expect(yaml).toContain('zone: [[-0.1750, -0.0250], [-0.0750, 0.0750], [0.0750, 0.2250]]')
    expect(yaml).toContain('node_id: transfer_left_to_right')
    expect(yaml).toContain('allowed_command: none')
    expect(yaml).toContain('node_id: place_right')
    expect(yaml).toContain('allowed_command: open')
    expect(yaml).toContain('zone: [[0.0250, 0.1750], [-0.0750, 0.0750], [0.0750, 0.2250]]')
  })

  it('omits transient unnamed boundaries from generated YAML', () => {
    const cfg = defaultConfig('so101')
    cfg.boundaries = [
      ...cfg.boundaries,
      {
        name: '',
        layer: 'L2',
        type: 'single',
        nodes: [{
          node_id: 'task_gripper_command_guard',
          callback: 'task_gripper_command_guard',
          params: { close_threshold: 0.25 },
          fallback: 'emergency_stop',
          timeout_sec: null,
        }],
      },
    ]
    cfg.tasks[0].boundaries = [...cfg.tasks[0].boundaries, '']

    const yaml = generateYaml(cfg)
    expect(yaml).not.toContain('\n  :')
    expect(yaml).not.toContain('close_threshold: 0.2500')
  })

  it('includes workspace bounds when set (quick_start)', () => {
    const cfg = defaultConfig('quick_start')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('bounds:')
  })

  it('SO-101 template includes L1 motion callbacks (QP is mandatory)', () => {
    const cfg = defaultConfig('so101')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('workspace:')
    expect(yaml).toContain('joint_velocity_limit')
    expect(yaml).toContain('joint_position_limits')
    expect(yaml).not.toContain('ood_detector')
  })

  it('cameras use index_or_path key in generated YAML', () => {
    const cfg = defaultConfig('so101')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('index_or_path:')
    expect(yaml).not.toContain('index: 0')   // old key must not appear
  })

  it('does not emit input_space in generated YAML', () => {
    const yaml = generateYaml(defaultConfig('so101'))
    expect(yaml).not.toContain('input_space:')
  })

  it('ACT template does not include diffusion params', () => {
    const cfg = defaultConfig('so101')
    const yaml = generateYaml(cfg)
    expect(yaml).not.toContain('noise_scheduler_type')
    expect(yaml).not.toContain('num_inference_steps')
  })

  it('calibration_path appears in YAML when set', () => {
    const cfg = defaultConfig('so101')
    cfg.lerobot_calibration_path = '/mnt/dam_data/calibration'
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('calibration_path: /mnt/dam_data/calibration')
  })

  it('calibration_path is omitted from YAML when empty', () => {
    const cfg = defaultConfig('so101')
    cfg.lerobot_calibration_path = ''
    const yaml = generateYaml(cfg)
    expect(yaml).not.toContain('calibration_path:')
    expect(yaml).toContain('robot_type: so101_follower')
  })

  it('includes ros2 interfaces for ros2 template', () => {
    const cfg = defaultConfig('ros2_minimal')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('ros2')
    expect(yaml).toContain('/joint_states')
  })

  it('emits observation channels as telemetry interfaces for lerobot', () => {
    const cfg = defaultConfig('so101')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('current:')
    // Telemetry channels omit `type:` — the key name is the channel type.
    expect(yaml).toMatch(/current:\s*\n\s*capabilities: \[robot_telemetry\]\s*\n\s*ref: arm/)
    expect(yaml).toMatch(/temperature:\s*\n\s*capabilities: \[robot_telemetry\]\s*\n\s*ref: arm/)
    expect(yaml).toMatch(/voltage:\s*\n\s*capabilities: \[robot_telemetry\]\s*\n\s*ref: arm/)
    expect(yaml).not.toMatch(/type: temperature/)
  })

  it('emits observation channels as telemetry interfaces for ros2', () => {
    const cfg = defaultConfig('ros2_minimal')
    const yaml = generateYaml(cfg)
    expect(yaml).toMatch(/effort:\s*\n\s*capabilities: \[robot_telemetry\]\s*\n\s*ref: ros2_joint_state/)
    expect(yaml).toMatch(/wrench:\s*\n\s*capabilities: \[robot_telemetry\]\s*\n\s*ref: ros2_joint_state/)
  })

  it('includes adapter section for lerobot', () => {
    const cfg = defaultConfig('so101')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('arm:')
    expect(yaml).toContain('type: motor')
  })

  it('uses correct control frequency', () => {
    const cfg = defaultConfig('ros2_minimal')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('control_hz: 30')
  })

  it('includes enforcement_mode in safety section', () => {
    const cfg = defaultConfig('so101')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('enforcement_mode: enforce')
  })

  it('does not include OOD boundary in the default so101 Stackfile', () => {
    const cfg = defaultConfig('so101')
    const yaml = generateYaml(cfg)
    expect(yaml).not.toContain('ood_detector:')
    expect(yaml).not.toContain('callback: ood_detector')
  })

  it('disabled guard appears as enabled: false in guards section', () => {
    const cfg = defaultConfig('so101')
    cfg.guardsEnabled = { execution: false }
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('enabled: false')
  })


  it('OOD callback boundary node params appear in boundaries when set', () => {
    const cfg = defaultConfig('so101')
    // Simulate OODTrainer selecting a model (boundary node is added via guard page)
    cfg.boundaries = [
      ...cfg.boundaries,
      {
        name: 'ood_detector',
        layer: 'L0',
        type: 'single',
        nodes: [{
          node_id: 'default',
          callback: 'ood_detector',
          params: { backend: 'memory_bank', ood_model_path: '/models/ood.pt', nn_threshold: 0.4, bank_path: '/models/ood_bank.npz' },
          fallback: 'emergency_stop',
          timeout_sec: null,
        }],
      },
    ]
    const yaml = generateYaml(cfg)
    // OOD params appear in boundaries, NOT in guards section
    expect(yaml).toContain('ood_detector')
    expect(yaml).toContain('backend: memory_bank')
    expect(yaml).toContain('/models/ood.pt')
    expect(yaml).toContain('nn_threshold')
    // Verify it is in the boundaries block (before tasks block)
    const guardsEnd = yaml.indexOf('\nboundaries:')
    const oodParamPos = yaml.indexOf('ood_model_path')
    expect(oodParamPos).toBeGreaterThan(-1)
    expect(oodParamPos).toBeGreaterThan(guardsEnd)
  })
})

describe('observation channel round-trip', () => {
  it('preserves observation_channels + channel_topic_overrides through emit→parse', () => {
    const cfg = defaultConfig('ros2_minimal')
    cfg.channel_topic_overrides = { wrench: '/my_robot/ft_sensor/wrench' }
    const yaml = generateYaml(cfg)

    // effort is JointState-derived → no topic line; wrench has its own topic
    expect(yaml).toMatch(/effort:\s*\n\s*capabilities: \[robot_telemetry\]\s*\n\s*ref: ros2_joint_state\s*(?!\s*topic:)/)
    expect(yaml).toMatch(/wrench:\s*\n\s*capabilities: \[robot_telemetry\]\s*\n\s*ref: ros2_joint_state\s*\n\s*topic: \/my_robot\/ft_sensor\/wrench/)

    const parsed = parseConfigFromYaml(yaml)
    expect(parsed.observation_channels).toEqual(['effort', 'wrench'])
    expect(parsed.channel_topic_overrides).toEqual({
      wrench: '/my_robot/ft_sensor/wrench',
    })
  })

  it('omits topic line when no override is set', () => {
    const cfg = defaultConfig('ros2_minimal')  // has channels but no overrides
    const yaml = generateYaml(cfg)

    // ros2_minimal declares effort + wrench with NO topic overrides
    expect(yaml).toContain('effort:')
    expect(yaml).toContain('wrench:')
    // No `topic:` line under either channel block
    expect(yaml).not.toMatch(/wrench:\s*\n\s*capabilities: \[robot_telemetry\]\s*\n\s*ref: ros2_joint_state\s*\n\s*topic:/)

    const parsed = parseConfigFromYaml(yaml)
    expect(parsed.observation_channels).toEqual(['effort', 'wrench'])
    expect(parsed.channel_topic_overrides).toBeUndefined()
  })

  it('lerobot health channels round-trip without overrides', () => {
    const cfg = defaultConfig('so101')
    const yaml = generateYaml(cfg)
    expect(yaml).toMatch(/current:\s*\n\s*capabilities: \[robot_telemetry\]\s*\n\s*ref: arm/)
    expect(yaml).toMatch(/temperature:\s*\n\s*capabilities: \[robot_telemetry\]\s*\n\s*ref: arm/)
    expect(yaml).toMatch(/voltage:\s*\n\s*capabilities: \[robot_telemetry\]\s*\n\s*ref: arm/)

    const parsed = parseConfigFromYaml(yaml)
    expect(parsed.observation_channels).toEqual(['current', 'temperature', 'voltage'])
    expect(parsed.channel_topic_overrides).toBeUndefined()
  })

  it('blank or duplicate channel names are dropped by the emitter', () => {
    const cfg = defaultConfig('ros2_minimal')
    cfg.observation_channels = ['effort', '', 'effort', 'wrench']
    const yaml = generateYaml(cfg)

    // effort emitted once, wrench once, empty skipped
    const effortMatches = yaml.match(/^\s{4}effort:$/gm) ?? []
    const wrenchMatches = yaml.match(/^\s{4}wrench:$/gm) ?? []
    expect(effortMatches).toHaveLength(1)
    expect(wrenchMatches).toHaveLength(1)
    expect(yaml).not.toMatch(/^\s{4}:$/m)

    const parsed = parseConfigFromYaml(yaml)
    expect(parsed.observation_channels).toEqual(['effort', 'wrench'])
  })
})

describe('slow lane + telemetry round-trip', () => {
  it('omits slow_lane and telemetry_hz by default', () => {
    const yaml = generateYaml(defaultConfig('so101'))
    expect(yaml).not.toMatch(/slow_lane:/)
    expect(yaml).not.toMatch(/telemetry_hz:/)
  })

  it('emits and parses slow_lane block', () => {
    const cfg = defaultConfig('so101')
    cfg.slowLane = { frequency_hz: 10, max_staleness_ms: 500, stale_action: 'reject' }
    const yaml = generateYaml(cfg)
    expect(yaml).toMatch(/slow_lane:\s*\n\s*task_hz: 10\s*\n\s*max_staleness_ms: 500\s*\n\s*stale_action: reject/)

    const parsed = parseConfigFromYaml(yaml)
    expect(parsed.slowLane).toEqual({ frequency_hz: 10, max_staleness_ms: 500, stale_action: 'reject' })
  })

  it('emits and parses telemetry_hz', () => {
    const cfg = defaultConfig('so101')
    cfg.telemetryHz = 5
    const yaml = generateYaml(cfg)
    expect(yaml).toMatch(/telemetry_hz: 5/)
    expect(parseConfigFromYaml(yaml).telemetryHz).toBe(5)
  })

  it('emits and parses per-guard lane override', () => {
    const cfg = defaultConfig('so101')
    cfg.guardRouting = { ...cfg.guardRouting, motion: { ...(cfg.guardRouting?.motion ?? {}), lane: 'slow' } }
    const yaml = generateYaml(cfg)
    expect(yaml).toMatch(/L1: motion[\s\S]*?lane: slow/)

    const parsed = parseConfigFromYaml(yaml)
    expect(parsed.guardRouting?.motion?.lane).toBe('slow')
  })
})
