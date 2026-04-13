/**
 * POST /api/system/restart
 *
 * 1. Writes the YAML stackfile to .dam_stackfile.yaml (project root).
 * 2. Tries to restart an already-running backend via POST /api/control/restart.
 *    If the backend is not running (ECONNREFUSED / timeout), falls back to
 *    spawning scripts/dev_server.py directly — so this endpoint doubles as a
 *    "Start" button for local development.
 *
 * Body: { yaml?: string; adapter?: string }
 */
import { spawn } from 'child_process'
import { writeFileSync } from 'fs'
import path from 'path'
import { NextRequest, NextResponse } from 'next/server'

const PROJECT_ROOT =
  process.env.DAM_PROJECT_ROOT ?? path.resolve(process.cwd(), '..')

const BACKEND_URL =
  process.env.DAM_INTERNAL_API_URL
  ?? process.env.NEXT_PUBLIC_API_URL
  ?? 'http://localhost:8080'

const BACKEND_RESTART_URL = `${BACKEND_URL}/api/control/restart`

/** Spawn dev_server.py detached — returns immediately. */
function spawnDevServer(): void {
  const script = path.join(PROJECT_ROOT, 'scripts', 'dev_server.py')
  const child = spawn('python3', [script], {
    cwd: PROJECT_ROOT,
    detached: true,
    stdio: 'ignore',
  })
  child.unref()
}

/**
 * Poll the backend health endpoint up to `maxMs` ms.
 * Returns true if it came online, false if it never responded.
 */
async function waitForBackend(maxMs = 8000, intervalMs = 500): Promise<boolean> {
  const deadline = Date.now() + maxMs
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${BACKEND_URL}/api/control/status`, {
        signal: AbortSignal.timeout(400),
      })
      if (res.ok) return true
    } catch { /* not up yet */ }
    await new Promise(r => setTimeout(r, intervalMs))
  }
  return false
}

/** True when the error is a connection-level failure (backend not running). */
function isOffline(err: unknown): boolean {
  if (!(err instanceof Error)) return false
  const msg = err.message.toLowerCase()
  return (
    msg.includes('econnrefused') ||
    msg.includes('fetch failed') ||
    msg.includes('network') ||
    msg.includes('timeout') ||
    msg.includes('abort')
  )
}

export async function POST(req: NextRequest) {
  try {
    let yaml = ''
    let adapter = 'simulation'
    try {
      const body = await req.json() as { yaml?: string; adapter?: string }
      if (body.yaml)    yaml    = body.yaml
      if (body.adapter) adapter = body.adapter
    } catch { /* no body */ }

    // Step 1 — persist the new stackfile so the (re)started process picks it up
    if (yaml) {
      writeFileSync(path.join(PROJECT_ROOT, '.dam_stackfile.yaml'), yaml, 'utf-8')
    }

    // Step 2 — try hot-restart on already-running backend
    try {
      const res = await fetch(BACKEND_RESTART_URL, {
        method: 'POST',
        signal: AbortSignal.timeout(3000),
      })
      if (res.ok) {
        return NextResponse.json({ ok: true, method: 'hot-restart' })
      }
      // Backend returned an error — fall through to cold start
    } catch (err) {
      if (!isOffline(err)) {
        // Unexpected network error — still fall through to cold start
      }
    }

    // Step 3 — backend not reachable: start it fresh.
    // dev_server.py always stays alive even when hardware is missing,
    // so we just poll until the health endpoint responds.
    if (adapter === 'simulation' || adapter === 'lerobot' || adapter === 'ros2') {
      spawnDevServer()
      const online = await waitForBackend(10000)
      if (online) {
        return NextResponse.json({
          ok: true,
          method: 'cold-start',
          message: 'Backend started. Dashboard will update shortly.',
        })
      }
      const hwError = 'Backend did not respond after 10 s. Check server logs.'
      return NextResponse.json({ ok: false, error: hwError }, { status: 503 })
    }

    return NextResponse.json({ ok: false, error: 'Backend is offline and no auto-start available for this adapter.' }, { status: 503 })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ ok: false, error: msg }, { status: 500 })
  }
}
