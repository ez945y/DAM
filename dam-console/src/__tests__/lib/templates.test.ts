import {
  TEMPLATES,
  defaultConfig,
  generateYaml,
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
})

describe('defaultConfig', () => {
  it('returns a valid config for so101_act', () => {
    const cfg = defaultConfig('so101_act')
    expect(cfg.joints).toHaveLength(6)
    expect(cfg.adapter).toBe('lerobot')
    expect(cfg.policy.type).toBe('act')
    expect(cfg.hardware_preset).toBe('so101_follower')
    expect(cfg.enforcement_mode).toBe('enforce')
  })

  it('SO-101 ACT uses correct pretrained_path', () => {
    const cfg = defaultConfig('so101_act')
    expect(cfg.policy.pretrained_path).toBe('MikeChenYZ/act-soarm-fmb-v2')
    expect(cfg.policy.device).toBe('mps')
  })

  it('SO-101 Diffusion has noise scheduler params', () => {
    const cfg = defaultConfig('so101_diffusion')
    expect(cfg.policy.type).toBe('diffusion')
    expect(cfg.policy.noise_scheduler_type).toBe('DDIM')
    expect(cfg.policy.num_inference_steps).toBe(15)
    expect(cfg.policy.pretrained_path).toBe('MikeChenYZ/dp-soarm-fmb')
  })

  it('SO-101 joints have correct names', () => {
    const cfg = defaultConfig('so101_act')
    const names = cfg.joints.map(j => j.name)
    expect(names).toContain('shoulder_pan')
    expect(names).toContain('gripper')
  })

  it('SO-101 joints use calibrated limits', () => {
    const cfg = defaultConfig('so101_act')
    const pan  = cfg.joints.find(j => j.name === 'shoulder_pan')!
    const grip = cfg.joints.find(j => j.name === 'gripper')!
    // shoulder_pan: ±1.8243
    expect(pan.lower_rad).toBeCloseTo(-1.8243, 4)
    expect(pan.upper_rad).toBeCloseTo( 1.8243, 4)
    // gripper: 0 → 1.7453 (one-directional)
    expect(grip.lower_rad).toBeCloseTo(0, 4)
    expect(grip.upper_rad).toBeCloseTo(1.7453, 4)
  })

  it('SO-101 robot_id matches lerobot-record default', () => {
    const cfg = defaultConfig('so101_act')
    expect(cfg.lerobot_robot_id).toBe('my_awesome_follower_arm')
  })

  it('SO-101 cameras use index_or_path convention', () => {
    const cfg = defaultConfig('so101_act')
    expect(cfg.lerobot_cameras).toHaveLength(2)
    expect(cfg.lerobot_cameras[0].name).toBe('top')
    expect(cfg.lerobot_cameras[1].name).toBe('wrist')
  })

  it('lerobot_calibration_path defaults to empty string', () => {
    const cfg = defaultConfig('so101_act')
    expect(cfg.lerobot_calibration_path).toBe('')
  })

  it('returns a simulation config for quick_start template', () => {
    const cfg = defaultConfig('quick_start')
    expect(cfg.adapter).toBe('simulation')
    expect(cfg.enforcement_mode).toBe('monitor')
    expect(cfg.tasks[0].boundaries).toHaveLength(5)
    expect(cfg.tasks[0].boundaries).toContain('hardware_watchdog')
  })

  it('falls back to simulation for unknown template id', () => {
    const cfg = defaultConfig('nonexistent')
    expect(cfg.adapter).toBe('simulation')
  })

  it('ros2 config has correct fields', () => {
    const cfg = defaultConfig('ros2_minimal')
    expect(cfg.adapter).toBe('ros2')
    expect(cfg.ros2JointTopic).toBe('/joint_states')
    expect(cfg.controlFrequencyHz).toBe(15)
  })

  it('guardsEnabled defaults to empty object', () => {
    const cfg = defaultConfig('quick_start')
    expect(cfg.guardsEnabled).toBeDefined()
  })
})

describe('generateYaml', () => {
  it('produces valid YAML string with required sections', () => {
    const cfg = defaultConfig('so101_act')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('version: "1"')
    expect(yaml).toContain('guards:')
    expect(yaml).toContain('safety:')
    expect(yaml).toContain('boundaries:')
    expect(yaml).toContain('tasks:')
  })

  it('includes hardware section for lerobot', () => {
    const cfg = defaultConfig('so101_act')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('hardware:')
    expect(yaml).toContain('so101_follower')
  })

  it('includes policy section for non-noop', () => {
    const cfg = defaultConfig('so101_act')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('policy:')
    expect(yaml).toContain('type: act')
  })

  it('quick_start uses hardware preset: simulation (new unified format)', () => {
    const cfg = defaultConfig('quick_start')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('hardware:')
    expect(yaml).toContain('preset: simulation')
    expect(yaml).toContain('sources:')
    expect(yaml).toContain('type: dataset')
    expect(yaml).toContain('MikeChenYZ/soarm-fmb-v2')
    expect(yaml).toContain('sinks:')
    expect(yaml).toContain('ref: sources.main')
  })

  it('omits USB section entirely (USB config removed from stackfile)', () => {
    const yaml = generateYaml(defaultConfig('so101_act'))
    expect(yaml).not.toContain('usb_devices:')
  })

  it('guards section contains list of active guards — no guard-specific params', () => {
    const cfg = defaultConfig('so101_act')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('guards:')
    expect(yaml).toContain('  - L0: ood')
    expect(yaml).toContain('  - L1: preflight')
    expect(yaml).toContain('  - L2: motion')
    expect(yaml).toContain('  - L3: execution')
    expect(yaml).toContain('  - L4: hardware')
    expect(yaml).not.toContain('upper_limits:')
    expect(yaml).not.toContain('lower_limits:')
    expect(yaml).not.toContain('ood_model_path:')
    expect(yaml).not.toContain('nn_threshold:')
  })

  it('includes all 5 builtin guards in list format (Perception-Monitoring)', () => {
    const cfg = defaultConfig('so101_act')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('- L0: ood')
    expect(yaml).toContain('- L1: preflight')
    expect(yaml).toContain('- L2: motion')
    expect(yaml).toContain('- L3: execution')
    expect(yaml).toContain('- L4: hardware')
  })

  it('joint limits appear in boundaries with calibrated values', () => {
    const cfg = defaultConfig('so101_act')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('joint_position_limits:')
    expect(yaml).toContain('upper:')
    expect(yaml).toContain('lower:')
    // shoulder_pan upper limit
    expect(yaml).toContain('1.8243')
    // gripper lower limit is 0
    expect(yaml).toContain('0')
  })

  it('includes workspace bounds in boundaries', () => {
    const cfg = defaultConfig('so101_act')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('bounds:')
    expect(yaml).toContain('hardware_watchdog:')
  })

  it('includes workspace bounds when set (quick_start)', () => {
    const cfg = defaultConfig('quick_start')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('bounds:')
  })

  it('cameras use index_or_path key in generated YAML', () => {
    const cfg = defaultConfig('so101_act')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('index_or_path:')
    expect(yaml).not.toContain('index: 0')   // old key must not appear
  })

  it('diffusion template includes noise_scheduler_type and num_inference_steps', () => {
    const cfg = defaultConfig('so101_diffusion')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('noise_scheduler_type: DDIM')
    expect(yaml).toContain('num_inference_steps: 15')
  })

  it('ACT template does not include diffusion params', () => {
    const cfg = defaultConfig('so101_act')
    const yaml = generateYaml(cfg)
    expect(yaml).not.toContain('noise_scheduler_type')
    expect(yaml).not.toContain('num_inference_steps')
  })

  it('calibration_path appears in YAML when set', () => {
    const cfg = defaultConfig('so101_act')
    cfg.lerobot_calibration_path = '/mnt/dam_data/calibration'
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('calibration_path: /mnt/dam_data/calibration')
  })

  it('calibration_path is omitted from YAML when empty', () => {
    const cfg = defaultConfig('so101_act')
    cfg.lerobot_calibration_path = ''
    const yaml = generateYaml(cfg)
    expect(yaml).not.toContain('calibration_path:')
  })

  it('includes ros2 source section for ros2 template', () => {
    const cfg = defaultConfig('ros2_minimal')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('ros2')
    expect(yaml).toContain('/joint_states')
  })

  it('includes adapter section for lerobot', () => {
    const cfg = defaultConfig('so101_act')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('follower_arm:')
    expect(yaml).toContain('type: lerobot')
  })

  it('uses correct control frequency', () => {
    const cfg = defaultConfig('ros2_minimal')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('control_frequency_hz: 15')
  })

  it('includes enforcement_mode in safety section', () => {
    const cfg = defaultConfig('so101_act')
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('enforcement_mode: enforce')
  })

  it('disabled guard appears as enabled: false in guards section', () => {
    const cfg = defaultConfig('so101_act')
    cfg.guardsEnabled = { ood: false }
    const yaml = generateYaml(cfg)
    expect(yaml).toContain('enabled: false')
  })


  it('ood_detector boundary node params appear in boundaries when set', () => {
    const cfg = defaultConfig('so101_act')
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
          params: { ood_model_path: '/models/ood.pt', nn_threshold: 0.4, backend: 'memory_bank' },
          fallback: 'emergency_stop',
          timeout_sec: null,
        }],
      },
    ]
    const yaml = generateYaml(cfg)
    // OOD params appear in boundaries, NOT in guards section
    expect(yaml).toContain('ood_detector')
    expect(yaml).toContain('/models/ood.pt')
    expect(yaml).toContain('nn_threshold')
    // Verify it is in the boundaries block (before tasks block)
    const guardsEnd = yaml.indexOf('\nboundaries:')
    const oodParamPos = yaml.indexOf('ood_model_path')
    expect(oodParamPos).toBeGreaterThan(-1)
    expect(oodParamPos).toBeGreaterThan(guardsEnd)
  })
})
