'use client'
import { useState, useEffect } from 'react'
import { Plus, Trash2, RefreshCw, Usb, Info } from 'lucide-react'
import { scanUsbDevices } from '@/lib/api'
import type { UsbDeviceInfo } from '@/lib/types'

type UsbDevice = { path: string }

interface Props {
  readonly devices: UsbDevice[]
  readonly onChange: (devices: UsbDevice[]) => void
}

const COMMON_PRESETS: { path: string; label: string }[] = [
  { path: '/dev/ttyACM0', label: 'ACM0 (robot arm)' },
  { path: '/dev/ttyACM1', label: 'ACM1' },
  { path: '/dev/ttyUSB0', label: 'USB0 (serial)' },
  { path: '/dev/video0',  label: 'video0 (camera)' },
  { path: '/dev/video1',  label: 'video1' },
]

export function UsbDeviceManager({ devices, onChange }: Props) {
  const [scannedDevices, setScannedDevices] = useState<UsbDeviceInfo[]>([])
  const [scanning, setScanning] = useState(false)
  const [dockerEnv, setDockerEnv] = useState(false)

  const doScan = async () => {
    setScanning(true)
    setDockerEnv(false)
    try {
      const result = await scanUsbDevices()
      const checkedPaths = new Set(devices.map(d => d.path))
      setScannedDevices(
        result.devices.map(d => ({ ...d, selected: checkedPaths.has(d.path) }))
      )
      if (result.count === 0) {
        // Heuristic: no devices found — likely Docker or no hardware
        setDockerEnv(true)
      }
    } catch {
      // Network error or endpoint unavailable — likely Docker with no host access
      setDockerEnv(true)
    } finally {
      setScanning(false)
    }
  }

  // Auto-scan on mount
  useEffect(() => {
    doScan()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const toggleScanned = (path: string, checked: boolean) => {
    setScannedDevices(prev => prev.map(d => d.path === path ? { ...d, selected: checked } : d))
    if (checked) {
      if (!devices.some(d => d.path === path)) {
        onChange([...devices, { path }])
      }
    } else {
      onChange(devices.filter(d => d.path !== path))
    }
  }

  const addPreset = (path: string) => {
    if (!devices.some(d => d.path === path)) {
      onChange([...devices, { path }])
    }
  }

  const addCustom = () => {
    onChange([...devices, { path: '/dev/ttyACM0' }])
  }

  const remove = (i: number) => onChange(devices.filter((_, idx) => idx !== i))

  const update = (i: number, value: string) => {
    const next = devices.map((d, idx) => idx === i ? { path: value } : d)
    onChange(next)
  }

  const typeBadgeClass = (type: UsbDeviceInfo['type']) =>
    type === 'serial'
      ? 'bg-dam-blue/20 text-dam-blue border-dam-blue/30'
      : 'bg-dam-green/20 text-dam-green border-dam-green/30'

  return (
    <div className="space-y-4">
      {/* Scan section */}
      <div>
        <div className="flex items-center gap-2 mb-2">
          <p className="text-dam-muted text-[10px] uppercase tracking-widest">Detected Devices</p>
          <button
            onClick={doScan}
            disabled={scanning}
            className="flex items-center gap-1 text-[10px] text-dam-muted hover:text-dam-blue transition-colors ml-auto disabled:opacity-50"
          >
            <RefreshCw size={10} className={scanning ? 'animate-spin' : ''} />
            {scanning ? 'Scanning…' : 'Scan'}
          </button>
        </div>

        {scanning && (
          <p className="text-dam-muted text-xs italic">Scanning USB devices…</p>
        )}

        {!scanning && dockerEnv && (
          <div className="flex gap-2 p-2.5 rounded-lg bg-dam-surface-3 border border-dam-border text-[11px] text-dam-muted mb-3">
            <Info size={12} className="shrink-0 mt-0.5 text-dam-blue" />
            <span>
              Auto-scan works on native host only. In Docker, devices must be mapped via{' '}
              <code className="font-mono text-dam-text">devices:</code> in your{' '}
              <code className="font-mono text-dam-text">docker-compose.yml</code>. Use the presets
              below to add common paths manually.
            </span>
          </div>
        )}

        {!scanning && !dockerEnv && scannedDevices.length === 0 && (
          <p className="text-dam-muted text-xs italic">No devices detected. Click Scan to search.</p>
        )}

        {!scanning && scannedDevices.length > 0 && (
          <div className="space-y-1.5">
            {scannedDevices.map(d => (
              <label
                key={d.path}
                className="flex items-center gap-3 px-3 py-2 rounded-lg bg-dam-surface-2 border border-dam-border cursor-pointer hover:border-dam-blue/40 transition-colors"
              >
                <input
                  type="checkbox"
                  checked={d.selected}
                  onChange={e => toggleScanned(d.path, e.target.checked)}
                  className="accent-dam-blue"
                />
                <Usb size={11} className="text-dam-muted shrink-0" />
                <span className="font-mono text-xs text-dam-text flex-1">{d.path}</span>
                <span className="text-[10px] text-dam-muted">{d.label}</span>
                <span className={`text-[9px] px-1.5 py-0.5 rounded border font-mono ${typeBadgeClass(d.type)}`}>
                  {d.type}
                </span>
              </label>
            ))}
          </div>
        )}
      </div>

      {/* Quick-add presets */}
      <div>
        <p className="text-dam-muted text-[10px] uppercase tracking-widest mb-2">Quick Add</p>
        <div className="flex flex-wrap gap-1.5">
          {COMMON_PRESETS.map(p => {
            const already = devices.some(d => d.path === p.path)
            return (
              <button
                key={p.path}
                onClick={() => addPreset(p.path)}
                disabled={already}
                title={p.label}
                className={`px-2 py-0.5 rounded text-[10px] border font-mono transition-all ${
                  already
                    ? 'border-dam-blue/40 bg-dam-blue-dim text-dam-blue cursor-default'
                    : 'border-dam-border bg-dam-surface-3 text-dam-muted hover:border-dam-blue/60 hover:text-dam-text'
                }`}
              >
                {p.path.split('/').pop()}
              </button>
            )
          })}
        </div>
      </div>

      {/* Manual / custom devices */}
      {devices.length > 0 && (
        <div>
          <p className="text-dam-muted text-[10px] uppercase tracking-widest mb-2">Configured Devices</p>
          <div className="space-y-2">
            <div className="grid grid-cols-[1fr_auto] gap-2 text-[10px] text-dam-muted uppercase tracking-wider px-1">
              <span>Device path</span>
              <span />
            </div>
            {devices.map((d, i) => (
              <div key={d.path || i} className="grid grid-cols-[1fr_auto] gap-2 items-center">
                <input
                  value={d.path}
                  onChange={e => update(i, e.target.value)}
                  placeholder="/dev/ttyACM0"
                  className="bg-dam-surface-2 border border-dam-border rounded px-2 py-1.5 text-xs font-mono text-dam-text"
                />
                <button
                  onClick={() => remove(i)}
                  className="p-1.5 text-dam-muted hover:text-dam-red transition-colors"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      <button
        onClick={addCustom}
        className="flex items-center gap-1.5 text-xs text-dam-muted hover:text-dam-blue transition-colors"
      >
        <Plus size={12} /> Add custom device
      </button>
    </div>
  )
}
