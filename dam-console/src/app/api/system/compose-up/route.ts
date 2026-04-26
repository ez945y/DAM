/**
 * POST /api/system/compose-up
 *
 * Writes the user-supplied YAML stackfile then runs
 * `docker compose up -d <service>` from the project root.
 *
 * Pass `restart: true` in the body to stop-then-recreate an already-running
 * service (picks up a new stackfile without rebuilding the image).
 */
import { exec } from 'node:child_process'
import { writeFileSync } from 'node:fs'
import { promisify } from 'node:util'
import path from 'node:path'
import { NextRequest, NextResponse } from 'next/server'

const execAsync = promisify(exec)

const PROJECT_ROOT =
  process.env.DAM_PROJECT_ROOT ?? path.resolve(process.cwd(), '..')

const ADAPTER_SERVICES: Record<string, { service: string; profile?: string }> = {
  simulation: { service: 'api' },
  lerobot:    { service: 'api-lerobot', profile: 'lerobot' },
  ros2:       { service: 'api-ros2',    profile: 'ros2'    },
}

export async function POST(req: NextRequest) {
  try {
    let adapter = 'simulation'
    let yaml = ''
    let restart = false
    try {
      const body = await req.json() as { adapter?: string; yaml?: string; restart?: boolean }
      if (body.adapter)  adapter  = body.adapter
      if (body.yaml)     yaml     = body.yaml
      if (body.restart)  restart  = body.restart
    } catch { /* no body */ }

    // Write user stackfile so the container picks it up on start
    if (yaml) {
      const dest = path.join(PROJECT_ROOT, '.dam_stackfile.yaml')
      writeFileSync(dest, yaml, 'utf-8')
    }

    const { service, profile } = ADAPTER_SERVICES[adapter] ?? ADAPTER_SERVICES.simulation
    const profileFlag = profile ? `--profile ${profile} ` : ''

    // --force-recreate ensures the container restarts with the updated stackfile
    const upFlag = restart ? '--force-recreate' : ''
    const cmd = `docker compose ${profileFlag}up -d ${upFlag} ${service}`.trim()

    const { stdout, stderr } = await execAsync(cmd, {
      cwd: PROJECT_ROOT,
      timeout: 120_000,
    })
    return NextResponse.json({ ok: true, stdout, stderr })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ ok: false, error: msg }, { status: 500 })
  }
}
