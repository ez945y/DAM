import { act, fireEvent, render, screen } from '@testing-library/react'
import ConfigPage from '@/app/config/page'
import GuardPage from '@/app/guard/page'
import { api } from '@/lib/api'
import { defaultConfig, generateYaml } from '@/lib/templates'

jest.mock('@/lib/api', () => ({
  api: {
    listStackfiles: jest.fn(), getStackfile: jest.fn(), saveConfig: jest.fn(),
    saveStackfile: jest.fn(), restart: jest.fn(),
    getCallbacks: jest.fn(async () => ({ callbacks: [] })),
    getGuardCatalog: jest.fn(async () => ({ guards: [] })),
    getCallbackCatalog: jest.fn(async () => ({ groups: [] })),
    getFallbacks: jest.fn(async () => ({ fallbacks: [] })),
    getBoundaryTemplates: jest.fn(async () => ({ templates: [] })),
  },
  scanUsbDevices: jest.fn(async () => ({ devices: [] })),
  listPresets: jest.fn(async () => []),
}))

jest.mock('@/components/ActionShell', () => ({
  ActionShell: ({ children, yaml, onYamlChange, onApply }: any) => (
    <div>
      <textarea aria-label="YAML source" value={yaml} readOnly={!onYamlChange}
        onChange={e => onYamlChange?.(e.target.value)} />
      {onApply && <button onClick={onApply}>Apply config</button>}
      {children}
    </div>
  ),
}))
jest.mock('@/components/PresetManager', () => ({ PresetManager: () => null }))
jest.mock('@/components/JointLimitsTable', () => ({ JointLimitsTable: () => null }))
jest.mock('@/components/OODTrainer', () => ({ OODTrainer: () => null }))

const mockApi = api as jest.Mocked<typeof api>
const originalYaml = '# Keep this comment and advanced fields\nversion: "1"\nhardware:\n  adapter: simulation\n  solvers: {}\nboundaries: {}\ntasks: {}\nfallbacks: {}\n'

describe.each([
  ['Configuration', ConfigPage],
  ['Guard', GuardPage],
] as const)('%s stackfile loading', (_name, Page) => {
  beforeEach(() => {
    jest.useFakeTimers()
    jest.clearAllMocks()
    localStorage.clear()
    mockApi.listStackfiles.mockResolvedValue({
      entries: [{ name: 'empty', size_bytes: 0, modified_at: 0 }], live: { exists: true },
    })
  })

  afterEach(() => { jest.clearAllTimers(); jest.useRealTimers() })

  it('does not save defaults during a slow load or rewrite the loaded YAML', async () => {
    let resolve!: (value: Response) => void
    globalThis.fetch = jest.fn(() => new Promise<Response>(r => { resolve = r }))
    render(<Page />)
    await act(async () => { jest.advanceTimersByTime(1500) })
    expect(mockApi.saveConfig).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Apply config' })).not.toBeInTheDocument()
    await act(async () => {
      resolve({ ok: true, status: 200, text: async () => originalYaml } as Response)
    })
    await act(async () => { jest.advanceTimersByTime(1500) })
    expect(screen.getByLabelText('YAML source')).toHaveValue(originalYaml)
    expect(mockApi.saveConfig).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Apply config' })).toBeInTheDocument()
  })

  it('clears old rules when switching to an empty configuration', async () => {
    const populated = generateYaml(defaultConfig('quick_start'))
    globalThis.fetch = jest.fn(async () => ({
      ok: true, status: 200, text: async () => populated,
    } as Response))
    mockApi.getStackfile.mockResolvedValue(originalYaml)
    await act(async () => { render(<Page />) })
    expect(JSON.parse(localStorage.getItem('dam_config_v1')!).boundaries.length).toBeGreaterThan(0)
    const selector = screen.getByRole('option', { name: /Live ·/ }).parentElement!
    await act(async () => { fireEvent.change(selector, { target: { value: 'empty' } }) })
    await act(async () => { jest.advanceTimersByTime(1500) })
    const config = JSON.parse(localStorage.getItem('dam_config_v1')!)
    expect(config.boundaries).toEqual([])
    expect(config.tasks).toEqual([])
    expect(config.fallbacks).toEqual([])
    expect(screen.getByLabelText('YAML source')).toHaveValue(originalYaml)
    expect(mockApi.saveStackfile).not.toHaveBeenCalled()
  })
})

it('saves an explicit YAML edit after the configuration has loaded', async () => {
  jest.useFakeTimers()
  jest.clearAllMocks()
  localStorage.clear()
  mockApi.listStackfiles.mockResolvedValue({ entries: [], live: { exists: true } })
  globalThis.fetch = jest.fn(async () => ({
    ok: true, status: 200, text: async () => originalYaml,
  } as Response))
  try {
    await act(async () => { render(<ConfigPage />) })
    const edited = originalYaml + '# explicit edit\n'
    fireEvent.change(screen.getByLabelText('YAML source'), { target: { value: edited } })
    await act(async () => { jest.advanceTimersByTime(1000) })
    expect(mockApi.saveConfig).toHaveBeenCalledWith(edited)
  } finally {
    jest.clearAllTimers()
    jest.useRealTimers()
  }
})
