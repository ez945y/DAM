/**
 * POST /api/system/launch
 *
 * Spawns the appropriate backend process based on the configured adapter.
 * If a `yaml` string is provided in the request body it is written to
 * `.dam_stackfile.yaml` at the project root so that dev_server.py picks
 * it up via its auto-detect logic.
 */
import { spawn } from 'child_process'
import { writeFileSync } from 'fs'
import path from 'path'
import { NextRequest, NextResponse } from 'next/server'

// Support both local dev (cwd = dam-console/) and Docker (env var set by compose)
const PROJECT_ROOT =
  process.env.DAM_PROJECT_ROOT ?? path.resolve(process.cwd(), '..')

const ADAPTER_SCRIPTS: Record<string, string> = {
  simulation: 'scripts/dev_server.py',
  lerobot:    'scripts/dev_server.py',
  ros2:       'scripts/dev_server.py',
}

export async function POST(req: NextRequest) {
  try {
    let adapter = 'simulation'
    let yaml = ''
    try {
      const body = await req.json() as { adapter?: string; yaml?: string }
      if (body.adapter) adapter = body.adapter
      if (body.yaml)    yaml    = body.yaml
    } catch { /* no body */ }

    // Write user-supplied stackfile so dev_server.py picks it up
    if (yaml) {
      const dest = path.join(PROJECT_ROOT, '.dam_stackfile.yaml')
      writeFileSync(dest, yaml, 'utf-8')
    }

    const scriptName = ADAPTER_SCRIPTS[adapter] ?? ADAPTER_SCRIPTS.simulation
    const script = path.join(PROJECT_ROOT, scriptName)

    const child = spawn('python', [script], {
      cwd: PROJECT_ROOT,
      detached: true,
      stdio: 'ignore',
      env: { ...process.env, DAM_ADAPTER: adapter },
    })
    child.unref()

    return NextResponse.json({ ok: true, message: `Dev server starting… (adapter: ${adapter})` })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ ok: false, error: msg }, { status: 500 })
  }
}
