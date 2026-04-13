import { api } from '@/lib/api'

const mockFetch = jest.fn()
;(global as unknown as Record<string, unknown>).fetch = mockFetch

function ok(data: unknown, status = 200) {
  return Promise.resolve({
    ok: status < 400,
    status,
    json: () => Promise.resolve(data),
  } as Response)
}

describe('api', () => {
  beforeEach(() => mockFetch.mockReset())

  describe('getRiskLog', () => {
    it('calls /api/risk-log', async () => {
      mockFetch.mockReturnValue(ok({ events: [], count: 0 }))
      await api.getRiskLog()
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/risk-log'),
        expect.any(Object),
      )
    })

    it('appends rejected_only query param', async () => {
      mockFetch.mockReturnValue(ok({ events: [], count: 0 }))
      await api.getRiskLog({ rejected_only: true })
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('rejected_only=true'),
        expect.any(Object),
      )
    })
  })

  describe('listBoundaries', () => {
    it('calls /api/boundaries', async () => {
      mockFetch.mockReturnValue(ok({ boundaries: [] }))
      await api.listBoundaries()
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/boundaries'),
        expect.any(Object),
      )
    })
  })

  describe('createBoundary', () => {
    it('sends POST with body', async () => {
      mockFetch.mockReturnValue(ok({ name: 'ws', type: 'single', nodes: [] }))
      await api.createBoundary({ name: 'ws', type: 'single', nodes: [] })
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/boundaries'),
        expect.objectContaining({ method: 'POST' }),
      )
    })
  })

  describe('deleteBoundary', () => {
    it('sends DELETE', async () => {
      mockFetch.mockReturnValue(ok(undefined, 204))
      await api.deleteBoundary('ws')
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/boundaries/ws'),
        expect.objectContaining({ method: 'DELETE' }),
      )
    })
  })

  describe('control', () => {
    it('start sends POST to /api/control/start', async () => {
      mockFetch.mockReturnValue(ok({ started: true, state: 'running' }))
      await api.start({ task_name: 'default', n_cycles: -1 })
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/control/start'),
        expect.objectContaining({ method: 'POST' }),
      )
    })

    it('emergencyStop sends POST to /api/control/estop', async () => {
      mockFetch.mockReturnValue(ok({ emergency_stop: true, state: 'emergency' }))
      await api.emergencyStop()
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/control/estop'),
        expect.objectContaining({ method: 'POST' }),
      )
    })
  })

  describe('error handling', () => {
    it('throws on non-ok response', async () => {
      mockFetch.mockReturnValue(ok({ detail: 'Not found' }, 404))
      await expect(api.getStatus()).rejects.toThrow('Not found')
    })
  })
})
