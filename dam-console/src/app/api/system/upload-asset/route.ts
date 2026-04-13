/**
 * POST /api/system/upload-asset
 *
 * Receives a file (multipart/form-data) and saves it to the shared volume
 * mount so the Python backend can load it at the path stored in the Stackfile.
 *
 * Form fields
 * -----------
 * file      The binary file to save.
 * target    Destination sub-directory under DAM_DATA_ROOT.
 *           Allowed values: "calibration" | "ood_model"
 *
 * Response
 * --------
 * { ok: true,  path: "/mnt/dam_data/calibration/arm.json" }
 * { ok: false, error: "..." }
 *
 * Environment
 * -----------
 * DAM_DATA_ROOT  Mount-point shared with the Python container.
 *                Default: /mnt/dam_data   (set to ./tmp/dam_data for local dev)
 */
import { writeFileSync, mkdirSync } from 'fs'
import path from 'path'
import { NextRequest, NextResponse } from 'next/server'

const ALLOWED_TARGETS = new Set(['calibration', 'ood_model'])

const DAM_DATA_ROOT =
  process.env.DAM_DATA_ROOT ?? '/mnt/dam_data'

export async function POST(req: NextRequest) {
  try {
    const formData = await req.formData()
    const file = formData.get('file') as File | null
    const target = formData.get('target') as string | null

    if (!file) {
      return NextResponse.json({ ok: false, error: 'No file provided' }, { status: 400 })
    }
    if (!target || !ALLOWED_TARGETS.has(target)) {
      return NextResponse.json(
        { ok: false, error: `target must be one of: ${[...ALLOWED_TARGETS].join(', ')}` },
        { status: 400 },
      )
    }

    const destDir = path.join(DAM_DATA_ROOT, target)
    mkdirSync(destDir, { recursive: true })

    const fileName = path.basename(file.name)
    const destPath = path.join(destDir, fileName)

    const buffer = Buffer.from(await file.arrayBuffer())
    writeFileSync(destPath, buffer)

    return NextResponse.json({ ok: true, path: destPath })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ ok: false, error: msg }, { status: 500 })
  }
}
