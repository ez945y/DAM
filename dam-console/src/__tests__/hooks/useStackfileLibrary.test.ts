import { act, renderHook } from '@testing-library/react'
import { api } from '@/lib/api'
import { LIVE, useStackfileLibrary } from '@/hooks/useStackfileLibrary'

jest.mock('@/lib/api', () => ({
  api: {
    listStackfiles: jest.fn(), getStackfile: jest.fn(), saveConfig: jest.fn(),
    saveStackfile: jest.fn(), createStackfile: jest.fn(),
    renameStackfile: jest.fn(), deleteStackfile: jest.fn(),
  },
}))

const mockApi = api as jest.Mocked<typeof api>
const mockFetch = jest.fn()

function response(text: string, status = 200) {
  return { ok: status < 400, status, text: async () => text } as Response
}

describe('useStackfileLibrary', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    localStorage.clear()
    globalThis.fetch = mockFetch
    mockFetch.mockReset().mockResolvedValue(response('live yaml'))
    mockApi.listStackfiles.mockResolvedValue({ entries: [], live: { exists: true } })
  })

  afterEach(() => jest.restoreAllMocks())

  it('blocks saving until a delayed bootstrap finishes', async () => {
    let resolve!: (value: Response) => void
    mockFetch.mockReturnValue(new Promise<Response>(r => { resolve = r }))
    const { result } = renderHook(() => useStackfileLibrary())
    const loaded = jest.fn()
    let pending!: Promise<void>
    act(() => { pending = result.current.bootstrap(loaded) })
    expect(result.current.busy).toBe(true)
    await expect(result.current.save('defaults')).rejects.toThrow('finish loading')
    expect(mockApi.saveConfig).not.toHaveBeenCalled()
    await act(async () => { resolve(response('original')); await pending })
    expect(loaded).toHaveBeenCalledWith('original')
    expect(result.current.ready).toBe(true)
    await act(async () => { await result.current.save('edited') })
    expect(mockApi.saveConfig).toHaveBeenCalledWith('edited')
  })

  it('surfaces bootstrap failures and keeps saving blocked', async () => {
    mockFetch.mockResolvedValue(response('unavailable', 500))
    const { result } = renderHook(() => useStackfileLibrary())
    const loaded = jest.fn()
    await act(async () => { await result.current.bootstrap(loaded) })
    expect(loaded).not.toHaveBeenCalled()
    expect(result.current.error).toContain('500')
    expect(result.current.ready).toBe(false)
    await expect(result.current.save('defaults')).rejects.toThrow('finish loading')
    expect(mockApi.saveConfig).not.toHaveBeenCalled()
  })

  it('allows initializing a missing live file', async () => {
    mockFetch.mockResolvedValue(response('', 404))
    const { result } = renderHook(() => useStackfileLibrary())
    const loaded = jest.fn()
    await act(async () => { await result.current.bootstrap(loaded) })
    expect(result.current.ready).toBe(true)
    expect(loaded).toHaveBeenCalledWith('')
  })

  it('keeps the current target and content on a failed switch', async () => {
    localStorage.setItem('dam_stackfile_target', 'saved')
    mockApi.getStackfile.mockResolvedValue('saved yaml')
    const { result } = renderHook(() => useStackfileLibrary())
    await act(async () => { await result.current.bootstrap(jest.fn()) })
    mockFetch.mockResolvedValue(response('', 503))
    const loaded = jest.fn()
    await act(async () => { await result.current.switchTarget(LIVE, loaded) })
    expect(result.current.target).toBe('saved')
    expect(loaded).not.toHaveBeenCalled()
    expect(result.current.error).toContain('503')
  })

  it('rejects a stale save callback after switching targets', async () => {
    const { result } = renderHook(() => useStackfileLibrary())
    await act(async () => { await result.current.bootstrap(jest.fn()) })
    const staleSave = result.current.save
    mockApi.getStackfile.mockResolvedValue('other yaml')
    await act(async () => { await result.current.switchTarget('other', jest.fn()) })
    await expect(staleSave('old draft')).rejects.toThrow('finish loading')
    expect(mockApi.saveStackfile).not.toHaveBeenCalled()
    await act(async () => { await result.current.save('new draft') })
    expect(mockApi.saveStackfile).toHaveBeenCalledWith('other', 'new draft')
  })

  it('uses exclusive creation and leaves the editor intact on conflict', async () => {
    jest.spyOn(window, 'prompt').mockReturnValue('duplicate')
    mockApi.createStackfile.mockRejectedValue(new Error('stackfile already exists'))
    const { result } = renderHook(() => useStackfileLibrary())
    await act(async () => { await result.current.bootstrap(jest.fn()) })
    const loaded = jest.fn()
    await act(async () => { await result.current.create('new yaml', loaded) })
    expect(mockApi.createStackfile).toHaveBeenCalledWith('duplicate', 'new yaml')
    expect(mockApi.saveStackfile).not.toHaveBeenCalled()
    expect(result.current.target).toBe(LIVE)
    expect(loaded).not.toHaveBeenCalled()
    expect(result.current.error).toContain('already exists')
  })

  it('does not delete an entry when its replacement cannot be loaded', async () => {
    jest.spyOn(window, 'confirm').mockReturnValue(true)
    localStorage.setItem('dam_stackfile_target', 'saved')
    mockApi.getStackfile.mockResolvedValue('saved yaml')
    const { result } = renderHook(() => useStackfileLibrary())
    await act(async () => { await result.current.bootstrap(jest.fn()) })
    mockFetch.mockResolvedValue(response('', 500))
    await act(async () => { await result.current.del(jest.fn()) })
    expect(mockApi.deleteStackfile).not.toHaveBeenCalled()
    expect(result.current.target).toBe('saved')
  })
})
