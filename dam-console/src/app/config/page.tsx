'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import { Download, Upload, RefreshCw, Copy, Check, Plus, Trash2, Usb, RotateCcw, ShieldCheck, FolderOpen, AlertCircle } from 'lucide-react'
import { TEMPLATES, defaultConfig, generateYaml, parseConfigFromYaml } from '@/lib/templates'
import type { DamConfig, CameraConfig, LoopbackConfig } from '@/lib/templates'
import type { EnforcementMode } from '@/lib/types'
import type { UsbDeviceInfo } from '@/lib/types'
import { api, scanUsbDevices } from '@/lib/api'
import { TemplateGallery } from '@/components/TemplateGallery'
import { AdapterColumn, ADAPTERS, POLICIES } from '@/components/AdapterPicker'
import { JointLimitsTable } from '@/components/JointLimitsTable'
import { OODTrainer } from '@/components/OODTrainer'
import { ActionShell } from '@/components/ActionShell'

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="glass-card p-6 space-y-4">
      <h2 className="text-dam-muted text-xs uppercase tracking-widest font-semibold relative z-10">{title}</h2>
      <div className="relative z-10">
        {children}
      </div>
    </div>
  )
}

const inputCls =
  'bg-dam-surface-2 border border-dam-border rounded px-2 py-1.5 text-xs font-mono text-dam-text focus:outline-none focus:border-dam-blue/60 transition-colors'

const STORAGE_KEY      = 'dam_config_v1'
const YAML_STORAGE_KEY = 'dam_yaml_v1'

function loadSaved(): DamConfig {
  try {
    const raw = typeof window !== 'undefined' ? localStorage.getItem(STORAGE_KEY) : null
    if (raw) {
      const parsed = JSON.parse(raw) as DamConfig
      return { ...defaultConfig(), ...parsed, templateId: '' }
    }
  } catch { /* ignore */ }
  return defaultConfig()
}

type AssetTarget = 'calibration' | 'ood_model'

function AssetUploader({
  label,
  hint,
  target,
  currentPath,
  onPathSaved,
}: {
  label: string
  hint: string
  target: AssetTarget
  currentPath: string
  onPathSaved: (path: string) => void
}) {
  const ref = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [ok, setOk] = useState(false)

  const handleFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ''
    setUploading(true)
    setError(null)
    setOk(false)
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('target', target)
      const res = await fetch('/api/system/upload-asset', { method: 'POST', body: fd })
      const body = await res.json() as { ok: boolean; path?: string; error?: string }
      if (!body.ok || !body.path) throw new Error(body.error ?? 'Upload failed')
      onPathSaved(body.path)
      setOk(true)
      setTimeout(() => setOk(false), 3000)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="space-y-1">
      <label className="text-dam-muted text-xs">{label}</label>
      <div className="flex items-center gap-2">
        <input
          value={currentPath}
          onChange={e => onPathSaved(e.target.value)}
          placeholder="/mnt/dam_data/..."
          className={`flex-1 ${inputCls} font-mono`}
        />
        <button
          onClick={() => ref.current?.click()}
          disabled={uploading}
          className="flex items-center gap-1 px-2.5 py-1.5 bg-dam-surface-3 border border-dam-border text-dam-muted text-xs rounded hover:text-dam-text transition-colors disabled:opacity-50 shrink-0"
        >
          {uploading
            ? <><RefreshCw size={10} className="animate-spin" /> Uploading…</>
            : ok
              ? <><Check size={10} className="text-dam-green" /> Saved</>
              : <><FolderOpen size={10} /> Upload file</>
          }
        </button>
      </div>
      {error && (
        <p className="flex items-center gap-1 text-dam-red text-[10px]">
          <AlertCircle size={10} /> {error}
        </p>
      )}
      <p className="text-dam-muted text-[10px]">{hint}</p>
      <input ref={ref} type="file" onChange={handleFile} className="hidden" />
    </div>
  )
}

export default function ConfigPage() {
  // ── Server-safe initial state (no localStorage — avoids SSR/client hydration mismatch)
  // loadSaved() is applied in the mount effect below; until then both server
  // and client render the same default so React's hydration check passes.
  const [cfg, setCfg] = useState<DamConfig>(() => defaultConfig())
  const [yaml, setYaml] = useState(() => generateYaml(defaultConfig()))
  const [yamlDirty, setYamlDirty] = useState(false)
  const [saved, setSaved] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const [restartError, setRestartError] = useState<string | null>(null)
  const [restartOk, setRestartOk] = useState(false)
  const jsonInputRef = useRef<HTMLInputElement>(null)

  // ── Load saved config from localStorage after mount (post-hydration, client-only)
  useEffect(() => {
    const saved = loadSaved()
    setCfg(saved)
    // yaml will regenerate via the cfg-watcher effect below
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(cfg))
      setSaved(true)
      const t = setTimeout(() => setSaved(false), 1200)
      return () => clearTimeout(t)
    } catch { /* ignore */ }
  }, [cfg])

  const [saving, setSaving] = useState(false)
  const [lastSavedYaml, setLastSavedYaml] = useState('')

  const saveToBackend = useCallback(async (content: string) => {
    if (!content || content === lastSavedYaml) return
    setSaving(true)
    try {
      const res = await api.saveConfig(content)
      // If api.saveConfig doesn't throw, it's successful
      setLastSavedYaml(content)
    } catch (err) {
      console.error('Failed to save config:', err)
    } finally {
      setSaving(false)
    }
  }, [lastSavedYaml])

  useEffect(() => {
    try { localStorage.setItem(YAML_STORAGE_KEY, yaml) } catch { /* ignore */ }
    const t = setTimeout(() => {
      void saveToBackend(yaml)
    }, 800)
    return () => clearTimeout(t)
  }, [yaml, saveToBackend])

  const [usbDevices, setUsbDevices] = useState<UsbDeviceInfo[]>([])
  const [usbScanning, setUsbScanning] = useState(false)
  const [usbScanFailed, setUsbScanFailed] = useState(false)

  useEffect(() => {
    if (!yamlDirty) {
      setYaml(generateYaml(cfg))
    }
  }, [cfg, yamlDirty])

  const doScan = useCallback(async () => {
    setUsbScanning(true)
    setUsbScanFailed(false)
    try {
      const result = await scanUsbDevices()
      setUsbDevices(result.devices)
      if (result.devices.length === 0) setUsbScanFailed(true)
    } catch {
      setUsbScanFailed(true)
      setUsbDevices([])
    } finally {
      setUsbScanning(false)
    }
  }, [])

  useEffect(() => {
    doScan()
  }, [doScan])

  useEffect(() => {
    const fetchConfig = async () => {
      try {
        // We use raw fetch here because api.ts doesn't have a getRawConfig but let's target 8080
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8080'}/api/system/config`)
        if (res.ok) {
          const content = await res.text()
          setYaml(content)
          const parsed = parseConfigFromYaml(content)
          setCfg(prev => ({ ...prev, ...parsed }))
          setYamlDirty(false)
        }
      } catch (err) {
        // Silence fetch errors during startup/polling
      }
    }
    void fetchConfig()
  }, [])

  const handleTemplate = (id: string) => {
    const next = defaultConfig(id)
    const nextYaml = generateYaml(next)
    // FORCE identity removal for statelessness
    setCfg({ ...next, templateId: '' })
    setYaml(nextYaml)
    setYamlDirty(false)
    // Save immediately on template change
    void saveToBackend(nextYaml)
  }

  const set = <K extends keyof DamConfig>(key: K, value: DamConfig[K]) => {
    setCfg(prev => ({ ...prev, [key]: value }))
    setYamlDirty(false)
  }

  const handleAdapterChange = (field: 'adapter' | 'policy', value: string) => {
    if (field === 'adapter') {
      set('adapter', value as DamConfig['adapter'])
    } else {
      setCfg(prev => ({
        ...prev,
        policy: { ...prev.policy, type: value as DamConfig['policy']['type'] },
      }))
      setYamlDirty(false)
    }
  }

  const addCamera = () => {
    const next: CameraConfig[] = [
      ...cfg.lerobot_cameras,
      { name: `cam${cfg.lerobot_cameras.length}`, source_type: 'opencv', index: cfg.lerobot_cameras.length, width: 640, height: 480, fps: 30 },
    ]
    set('lerobot_cameras', next)
  }

  const removeCamera = (i: number) => {
    set('lerobot_cameras', cfg.lerobot_cameras.filter((_, idx) => idx !== i))
  }

  const updateCamera = (
    i: number,
    field: keyof CameraConfig,
    value: string | number,
  ) => {
    const next = cfg.lerobot_cameras.map((c, idx) =>
      idx === i ? { ...c, [field]: value } : c
    )
    set('lerobot_cameras', next)
  }

  const [restartMsg, setRestartMsg] = useState<string | null>(null)

  const handleApplyRestart = useCallback(async () => {
    setRestarting(true)
    setRestartError(null)
    setRestartOk(false)
    setRestartMsg(null)
    try {
      const adapter = cfg.adapter ?? 'simulation'
      await api.restart(adapter, yaml)
      setRestartOk(true)
      setTimeout(() => { setRestartOk(false); setRestartMsg(null) }, 5000)
    } catch (e) {
      setRestartError(e instanceof Error ? e.message : String(e))
    } finally {
      setRestarting(false)
    }
  }, [yaml, cfg.adapter])

  const handleYamlEdit = (v: string) => {
    setYaml(v)
    setYamlDirty(true)
    const parsed = parseConfigFromYaml(v)
    if (Object.keys(parsed).length > 0) {
      setCfg(prev => ({ ...prev, ...parsed }))
    }
  }

  const setCalibrationPath = (p: string) => {
    setCfg(prev => ({ ...prev, lerobot_calibration_path: p }))
    setYamlDirty(false)
  }

  const handleExportJson = () => {
    const blob = new Blob([JSON.stringify(cfg, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `dam_config_${cfg.templateId}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleImportJson = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = ev => {
      try {
        const parsed = JSON.parse(ev.target?.result as string) as DamConfig
        setCfg(parsed)
        setYaml(generateYaml(parsed))
        setYamlDirty(false)
      } catch { alert('Invalid JSON config file') }
    }
    reader.readAsText(file)
    e.target.value = ''
  }

  const ENFORCEMENT_MODES: { id: EnforcementMode; label: string; description: string }[] = [
    { id: 'enforce',  label: 'Enforce',  description: 'Actions blocked on reject' },
    { id: 'monitor',  label: 'Monitor',  description: 'Validates but does not block' },
    { id: 'log_only', label: 'Log only', description: 'Records only, no interference' },
  ]

  return (
    <ActionShell
      title="Configuration"
      description="System adapters, hardware presets & global settings"
      restarting={restarting}
      restartOk={restartOk}
      restartError={restartError}
      saved={saved}
      yaml={yaml}
      onYamlChange={handleYamlEdit}
      onApply={handleApplyRestart}
      onImport={() => jsonInputRef.current?.click()}
      onExport={handleExportJson}
    >
      <input ref={jsonInputRef} type="file" accept=".json" onChange={handleImportJson} className="hidden" />
      {/* Config Sections */}
      
      {restartMsg && (
        <div className="flex items-center gap-2 p-2 bg-dam-blue/10 border border-dam-blue/20 rounded text-[10px] text-dam-blue animate-in fade-in slide-in-from-right-1">
          <AlertCircle size={10} /> {restartMsg}
        </div>
      )}

      <Section title="Template">
        <TemplateGallery
          templates={TEMPLATES}
          selected={cfg.templateId}
          onSelect={handleTemplate}
        />
      </Section>

      <Section title="Hardware">
        {cfg.adapter !== 'simulation' && (
          <div className="flex items-center gap-3 flex-wrap">
            <label className="text-dam-muted text-xs shrink-0">Hardware preset:</label>
            {['so101_follower', 'generic_6dof', 'custom'].map(preset => (
              <button
                key={preset}
                onClick={() => set('hardware_preset', preset)}
                className={`px-2.5 py-1 rounded text-xs border transition-all ${
                  cfg.hardware_preset === preset
                    ? 'bg-dam-blue-dim border-dam-blue text-dam-blue'
                    : 'bg-dam-surface-2 border-dam-border text-dam-muted hover:border-dam-blue/40'
                }`}
              >
                {preset}
              </button>
            ))}
          </div>
        )}

        <AdapterColumn
          title="Hardware (Source + Sink)"
          options={ADAPTERS}
          selected={cfg.adapter}
          onSelect={v => handleAdapterChange('adapter', v)}
        />

        {cfg.adapter === 'lerobot' && (
          <div className="space-y-4 pt-2 border-t border-dam-border/60">
            <p className="text-dam-muted text-[10px] uppercase tracking-widest">LeRobot Settings</p>
            <div className="space-y-2">
              <label className="text-dam-muted text-xs">Robot Port</label>
              {usbDevices.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {usbDevices.map(dev => (
                    <button
                      key={dev.path}
                      onClick={() => set('lerobot_port', dev.path)}
                      title={dev.label}
                      className={`flex items-center gap-1.5 px-2 py-1 rounded text-xs border transition-all ${
                        cfg.lerobot_port === dev.path
                          ? 'bg-dam-blue-dim border-dam-blue text-dam-blue'
                          : 'bg-dam-surface-2 border-dam-border text-dam-muted hover:border-dam-blue/40'
                      }`}
                    >
                      <Usb size={10} />
                      <span className="font-mono">{dev.path}</span>
                    </button>
                  ))}
                </div>
              )}
              <div className="flex gap-2 items-center">
                <input
                  value={cfg.lerobot_port}
                  onChange={e => set('lerobot_port', e.target.value)}
                  placeholder="/dev/tty.usbmodem..."
                  className={`flex-1 ${inputCls}`}
                />
                <button
                  onClick={doScan}
                  disabled={usbScanning}
                  className="flex items-center gap-1 px-2.5 py-1.5 bg-dam-surface-3 border border-dam-border text-dam-muted text-xs rounded hover:text-dam-text transition-colors disabled:opacity-50"
                >
                  <RefreshCw size={10} className={usbScanning ? 'animate-spin' : ''} />
                  {usbScanning ? 'Scanning…' : 'Scan'}
                </button>
              </div>
            </div>

            <div className="space-y-1">
              <label className="text-dam-muted text-xs">Robot ID</label>
              <input
                value={cfg.lerobot_robot_id}
                onChange={e => set('lerobot_robot_id', e.target.value)}
                placeholder="my_follower_arm"
                className={`w-full ${inputCls}`}
              />
            </div>

            <div className="space-y-2">
              <label className="text-dam-muted text-xs">Joint Limits</label>
              <JointLimitsTable
                joints={cfg.joints}
                onChange={j => set('joints', j)}
              />
            </div>

            <div className="space-y-1">
              <label className="text-dam-muted text-xs">Calibration Directory Path</label>
              <p className="text-dam-muted text-[10px] mb-2">Provide the directory containing LeRobot calibration JSONs.</p>
              <AssetUploader
                label=""
                hint="Tip: You can also paste an absolute local path directly."
                target="calibration"
                currentPath={cfg.lerobot_calibration_path}
                onPathSaved={setCalibrationPath}
              />
            </div>

            <div>
              <p className="text-dam-muted text-[10px] uppercase tracking-widest mb-2">Cameras</p>
              {cfg.lerobot_cameras.length > 0 && (
                <div className="space-y-2 mb-2">
                  {cfg.lerobot_cameras.map((cam, i) => (
                    <div key={i} className="grid grid-cols-[6rem_5rem_6rem_4rem_4rem_3.5rem_auto] gap-1.5 items-center">
                      <input value={cam.name} onChange={e => updateCamera(i, 'name', e.target.value)} className={inputCls} placeholder="top" />
                      <select value={cam.source_type} onChange={e => updateCamera(i, 'source_type', e.target.value)} className={inputCls}>
                        <option value="opencv">opencv</option>
                        <option value="udp">udp</option>
                      </select>
                      {cam.source_type === 'udp'
                        ? <input value={cam.udp_url ?? ''} onChange={e => updateCamera(i, 'udp_url', e.target.value)} className={inputCls} placeholder="udp://..." />
                        : <input type="number" value={cam.index ?? 0} onChange={e => updateCamera(i, 'index', Number(e.target.value))} className={inputCls} />
                      }
                      <input type="number" value={cam.width} onChange={e => updateCamera(i, 'width', Number(e.target.value))} className={inputCls} />
                      <input type="number" value={cam.height} onChange={e => updateCamera(i, 'height', Number(e.target.value))} className={inputCls} />
                      <input type="number" value={cam.fps} onChange={e => updateCamera(i, 'fps', Number(e.target.value))} className={inputCls} />
                      <button onClick={() => removeCamera(i)} className="text-dam-muted hover:text-dam-red transition-colors"><Trash2 size={12} /></button>
                    </div>
                  ))}
                </div>
              )}
              <button onClick={addCamera} className="flex items-center gap-1 text-xs text-dam-muted hover:text-dam-blue transition-colors">
                <Plus size={11} /> Add camera
              </button>
            </div>
          </div>
        )}

        {cfg.adapter === 'ros2' && (
          <div className="space-y-4 pt-2 border-t border-dam-border/60">
            <p className="text-dam-muted text-[10px] uppercase tracking-widest">ROS2 Settings</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="space-y-1">
                <label className="text-dam-muted text-xs">Namespace</label>
                <input value={cfg.ros2Namespace} onChange={e => set('ros2Namespace', e.target.value)} className={`w-full ${inputCls}`} />
              </div>
              <div className="space-y-1">
                <label className="text-dam-muted text-xs">Joint states topic</label>
                <input value={cfg.ros2JointTopic} onChange={e => set('ros2JointTopic', e.target.value)} className={`w-full ${inputCls}`} />
              </div>
            </div>
          </div>
        )}

        {cfg.adapter === 'simulation' && (
          <div className="pt-2 border-t border-dam-border/60 space-y-3">
            <div className="space-y-1">
              <label className="text-dam-muted text-xs">Dataset repo (HuggingFace)</label>
              <input
                value={cfg.simulation_dataset_repo_id ?? ''}
                onChange={e => set('simulation_dataset_repo_id', e.target.value || undefined)}
                placeholder="e.g. MikeChenYZ/soarm-fmb-v2"
                className={`w-full ${inputCls}`}
              />
              <p className="text-dam-muted text-[10px]">Leave blank to use a random-walk fallback.</p>
            </div>
            <div className="space-y-1">
              <label className="text-dam-muted text-xs">Episode index</label>
              <input
                type="number"
                min={0}
                value={cfg.simulation_episode ?? 0}
                onChange={e => set('simulation_episode', Number(e.target.value))}
                className={`w-28 ${inputCls}`}
              />
            </div>
          </div>
        )}
      </Section>

      <Section title="Policy">
        <div className="mb-4">
          <AdapterColumn
            title="Policy Type"
            options={POLICIES}
            selected={cfg.policy.type}
            onSelect={v => handleAdapterChange('policy', v)}
          />
        </div>
        <div className="space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="space-y-1">
              <label className="text-dam-muted text-xs">Pretrained path</label>
              <input
                value={cfg.policy.pretrained_path}
                onChange={e => setCfg(prev => ({ ...prev, policy: { ...prev.policy, pretrained_path: e.target.value } }))}
                className={`w-full ${inputCls}`}
              />
            </div>
            <div className="space-y-1">
              <label className="text-dam-muted text-xs">Device</label>
              <select
                value={cfg.policy.device}
                onChange={e => setCfg(prev => ({ ...prev, policy: { ...prev.policy, device: e.target.value as DamConfig['policy']['device'] } }))}
                className={`w-full ${inputCls}`}
              >
                {['cpu', 'cuda', 'mps'].map(d => <option key={d} value={d}>{d}</option>)}
              </select>
            </div>
          </div>
        </div>
      </Section>

      <Section title="Safety">
        <div>
          <p className="text-dam-muted text-[10px] uppercase tracking-widest mb-2">Enforcement Mode</p>
          <div className="flex gap-1.5 flex-wrap">
            {ENFORCEMENT_MODES.map(m => (
              <button
                key={m.id}
                onClick={() => set('enforcement_mode', m.id)}
                className={`px-3 py-1.5 rounded text-xs border transition-all ${
                  cfg.enforcement_mode === m.id
                    ? 'bg-dam-blue-dim border-dam-blue text-dam-blue font-semibold'
                    : 'bg-dam-surface-2 border-dam-border text-dam-muted hover:border-dam-blue/40'
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>
        <div className="pt-3 border-t border-dam-border/60 mt-3">
          <div className="space-y-1">
            <label className="text-dam-muted text-xs">Control frequency (Hz)</label>
            <input
              type="number"
              step="5"
              min="1"
              value={cfg.controlFrequencyHz}
              onChange={e => set('controlFrequencyHz', Number(e.target.value))}
              className={`w-full ${inputCls}`}
            />
          </div>
        </div>
      </Section>

      <Section title="MCAP Recording (Loopback)">
        <div className="space-y-4">
          {/* Enable/Disable toggle */}
          <div className="flex items-center gap-3">
            <label className="text-dam-muted text-xs">Recording status:</label>
            <button
              onClick={() => setCfg(prev => ({
                ...prev,
                loopback: prev.loopback ? undefined : {
                  backend: 'mcap',
                  output_dir: './data/robot/sessions',
                  window_sec: 10.0,
                  rotate_mb: 500.0,
                  rotate_minutes: 60.0,
                  max_queue_depth: 64,
                  capture_images_on_clamp: true,
                },
              }))}
              className={`px-3 py-1.5 rounded text-xs font-semibold border transition-all ${
                cfg.loopback
                  ? 'bg-dam-green/10 border-dam-green/30 text-dam-green'
                  : 'bg-dam-surface-2 border-dam-border text-dam-muted hover:border-dam-orange/40'
              }`}
            >
              {cfg.loopback ? '✓ Enabled' : '○ Disabled'}
            </button>
          </div>

          {cfg.loopback && (
            <>
              {/* Output directory */}
              <div className="space-y-1">
                <label className="text-dam-muted text-xs">Output Directory</label>
                <input
                  value={cfg.loopback.output_dir}
                  onChange={e => setCfg(prev => ({
                    ...prev,
                    loopback: prev.loopback ? { ...prev.loopback, output_dir: e.target.value } : undefined,
                  }))}
                  placeholder="./data/robot/sessions"
                  className={`w-full ${inputCls}`}
                />
                <p className="text-dam-muted text-[10px]">Directory where MCAP session files will be stored</p>
              </div>

              {/* Backend selection */}
              <div className="space-y-1">
                <label className="text-dam-muted text-xs">Backend Format</label>
                <select
                  value={cfg.loopback.backend}
                  onChange={e => setCfg(prev => ({
                    ...prev,
                    loopback: prev.loopback ? { ...prev.loopback, backend: e.target.value as 'mcap' | 'pickle' } : undefined,
                  }))}
                  className={`w-full ${inputCls}`}
                >
                  <option value="mcap">MCAP (recommended)</option>
                  <option value="pickle">Pickle (legacy)</option>
                </select>
                <p className="text-dam-muted text-[10px]">MCAP format is recommended for better compatibility and compression</p>
              </div>

              {/* File rotation settings */}
              <div className="space-y-3 pt-3 border-t border-dam-border/30">
                <p className="text-dam-muted text-[10px] uppercase tracking-widest">File Rotation</p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <label className="text-dam-muted text-xs">Rotate after (MB)</label>
                    <input
                      type="number"
                      step="50"
                      min="10"
                      value={cfg.loopback.rotate_mb}
                      onChange={e => setCfg(prev => ({
                        ...prev,
                        loopback: prev.loopback ? { ...prev.loopback, rotate_mb: Number(e.target.value) } : undefined,
                      }))}
                      className={`w-full ${inputCls}`}
                    />
                    <p className="text-dam-muted text-[10px]">Create new file after this size</p>
                  </div>
                  <div className="space-y-1">
                    <label className="text-dam-muted text-xs">Rotate after (minutes)</label>
                    <input
                      type="number"
                      step="5"
                      min="1"
                      value={cfg.loopback.rotate_minutes}
                      onChange={e => setCfg(prev => ({
                        ...prev,
                        loopback: prev.loopback ? { ...prev.loopback, rotate_minutes: Number(e.target.value) } : undefined,
                      }))}
                      className={`w-full ${inputCls}`}
                    />
                    <p className="text-dam-muted text-[10px]">Create new file after this duration</p>
                  </div>
                </div>
              </div>

              {/* Queue and image settings */}
              <div className="space-y-3 pt-3 border-t border-dam-border/30">
                <p className="text-dam-muted text-[10px] uppercase tracking-widest">Buffering & Capture</p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <label className="text-dam-muted text-xs">Max queue depth</label>
                    <input
                      type="number"
                      step="8"
                      min="8"
                      value={cfg.loopback.max_queue_depth}
                      onChange={e => setCfg(prev => ({
                        ...prev,
                        loopback: prev.loopback ? { ...prev.loopback, max_queue_depth: Number(e.target.value) } : undefined,
                      }))}
                      className={`w-full ${inputCls}`}
                    />
                    <p className="text-dam-muted text-[10px]">Drop cycles if queue exceeds this depth</p>
                  </div>
                  <div className="space-y-1">
                    <label className="text-dam-muted text-xs">Image ring buffer (sec)</label>
                    <input
                      type="number"
                      step="1"
                      min="1"
                      value={cfg.loopback.window_sec}
                      onChange={e => setCfg(prev => ({
                        ...prev,
                        loopback: prev.loopback ? { ...prev.loopback, window_sec: Number(e.target.value) } : undefined,
                      }))}
                      className={`w-full ${inputCls}`}
                    />
                    <p className="text-dam-muted text-[10px]">Keep images from last N seconds for pre-event capture</p>
                  </div>

                  {/* Pre-event capture duration */}
                  <div className="space-y-1">
                    <label className="text-dam-muted text-xs">Pre-event Capture (seconds)</label>
                    <input
                      type="number"
                      min="0"
                      max="60"
                      value={cfg.loopback.pre_event_sec ?? 10}
                      onChange={e => setCfg(prev => ({
                        ...prev,
                        loopback: prev.loopback ? { ...prev.loopback, pre_event_sec: Number(e.target.value) } : undefined,
                      }))}
                      className={`w-full ${inputCls}`}
                    />
                    <p className="text-dam-muted text-[10px]">Capture N seconds before event (0 = capture all cycles)</p>
                  </div>
                </div>

                {/* Capture on clamp toggle */}
                <div className="flex items-center gap-3 pt-2 border-t border-dam-border/20">
                  <input
                    type="checkbox"
                    checked={cfg.loopback.capture_images_on_clamp}
                    onChange={e => setCfg(prev => ({
                      ...prev,
                      loopback: prev.loopback ? { ...prev.loopback, capture_images_on_clamp: e.target.checked } : undefined,
                    }))}
                    className="accent-dam-blue"
                  />
                  <label className="text-dam-muted text-xs flex-1">Capture images on CLAMP events</label>
                  <span className={`text-[10px] font-semibold ${cfg.loopback.capture_images_on_clamp ? 'text-dam-blue' : 'text-dam-muted'}`}>
                    {cfg.loopback.capture_images_on_clamp ? 'ON' : 'OFF'}
                  </span>
                </div>
              </div>
            </>
          )}

          {!cfg.loopback && (
            <div className="p-3 bg-dam-surface-2 border border-dam-border/60 rounded-lg">
              <p className="text-dam-muted text-[10px]">MCAP recording is currently disabled. Click "Enabled" to start recording control cycles, guard results, and captured images.</p>
            </div>
          )}
        </div>
      </Section>
    </ActionShell>
  )
}
