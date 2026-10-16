import { safetyComparisonApi } from '@/lib/safety-comparison-api'

const originalFetch = global.fetch
beforeEach(() => { global.fetch = jest.fn() })
afterEach(() => { global.fetch = originalFetch })

function response(status: number, body: string, statusText = '') {
  ;(global.fetch as jest.Mock).mockResolvedValue({ ok: status >= 200 && status < 300, status, statusText, text: async () => body })
}

test('reports an HTML gateway error as an HTTP error rather than a JSON parse exception', async () => {
  response(502, '<html>upstream failed</html>', 'Bad Gateway')
  await expect(safetyComparisonApi.cases()).rejects.toThrow('HTTP 502: Bad Gateway')
})

test('formats validation errors into readable messages', async () => {
  response(422, JSON.stringify({ detail: [{ msg: 'cycle_end must be an integer' }] }))
  await expect(safetyComparisonApi.cases()).rejects.toThrow('HTTP 422: cycle_end must be an integer')
})

test('rejects a malformed successful response explicitly', async () => {
  response(200, 'not JSON')
  await expect(safetyComparisonApi.cases()).rejects.toThrow('Server returned an invalid JSON response')
})
