/**
 * POST /api/system/save-config
 * Writes the YAML stackfile to .dam_stackfile.yaml at the project root.
 * Called automatically (debounced) whenever the config page YAML changes.
 */
import { writeFileSync } from 'node:fs'
import path from 'node:path'
import { NextRequest, NextResponse } from 'next/server'

const PROJECT_ROOT =
  process.env.DAM_PROJECT_ROOT || path.resolve(process.env.PWD || process.cwd(), '..')

export async function POST(req: NextRequest) {
  try {
    const { yaml } = await req.json() as { yaml?: string }
    const target = path.join(PROJECT_ROOT, '.dam_stackfile.yaml')
    console.log(`[save-config] Writing to: ${target}`)
    if (!yaml) return NextResponse.json({ ok: false, error: 'No yaml provided' }, { status: 400 })
    writeFileSync(target, yaml, 'utf-8')
    return NextResponse.json({ ok: true, path: target })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ ok: false, error: msg }, { status: 500 })
  }
}
