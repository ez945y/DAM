'use client'
import React, { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { McapFrame } from '@/lib/api'
import { ChevronLeft, ChevronRight, Loader2, Image as ImageIcon } from 'lucide-react'

export interface McapImageGalleryProps {
  sessionFilename: string
  cycleId: number
  images: Record<string, number>  // { camera_name: frame_idx }
}

/**
 * Image gallery for a selected cycle.
 * Shows JPEG frames captured for each camera.
 */
export function McapImageGallery({
  sessionFilename,
  cycleId,
  images,
}: McapImageGalleryProps) {
  const [selectedCam, setSelectedCam] = useState<string | null>(null)
  const [frameList, setFrameList] = useState<McapFrame[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const cameras = Object.keys(images ?? {}).sort((a, b) => a.localeCompare(b))

  // Auto-select first camera
  useEffect(() => {
    if (cameras.length > 0 && !selectedCam) {
      setSelectedCam(cameras[0])
    }
  }, [cameras, selectedCam])

  // Load frames when camera changes
  useEffect(() => {
    if (!selectedCam) return

    async function loadFrames() {
      if (!selectedCam) return
      try {
        setLoading(true)
        setError(null)
        const data = await api.listMcapFrames(sessionFilename, selectedCam)
        setFrameList(data?.frames ?? [])
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load frames')
      } finally {
        setLoading(false)
      }
    }

    loadFrames()
  }, [sessionFilename, selectedCam])

  if (cameras.length === 0) {
    return (
      <div className="py-8 text-center text-dam-muted">
        <ImageIcon size={32} className="mx-auto mb-2 opacity-50" />
        <p className="text-sm">No images captured for this cycle</p>
      </div>
    )
  }

  const currentFrameIdx = selectedCam ? images[selectedCam] : null

  return (
    <div className="space-y-3">
      {/* Camera selector */}
      <div className="flex gap-2">
        {cameras.map(cam => (
          <button
            key={cam}
            onClick={() => setSelectedCam(cam)}
            className={`px-3 py-1.5 text-xs font-medium rounded-lg border transition-all ${
              selectedCam === cam
                ? 'bg-dam-blue/10 text-dam-blue border-dam-blue/30'
                : 'bg-dam-surface-1 text-dam-muted border-dam-border hover:border-dam-blue/20'
            }`}
          >
            {cam}
          </button>
        ))}
      </div>

      {/* Image display */}
      {selectedCam && (
        <div className="bg-dam-surface-2 border border-dam-border rounded-lg overflow-hidden">
          {loading ? (
            <div className="flex items-center justify-center h-64 text-dam-muted">
              <Loader2 size={20} className="animate-spin mr-2" />
              Loading frames...
            </div>
          ) : error ? (
            <div className="flex items-center justify-center h-64 text-red-500 text-sm p-4 text-center">
              {error}
            </div>
          ) : currentFrameIdx !== null && frameList.length > 0 ? (
            <>
              {/* Image */}
              <div className="bg-black flex items-center justify-center" style={{ aspectRatio: '16/9' }}>
                <img
                  src={api.mcapFrameUrl(sessionFilename, selectedCam, currentFrameIdx)}
                  alt={`${selectedCam} frame ${currentFrameIdx}`}
                  className="max-w-full max-h-full object-contain"
                  loading="lazy"
                />
              </div>

              {/* Frame info */}
              <div className="p-3 border-t border-dam-border/30 flex items-center justify-between text-xs">
                <span className="text-dam-muted">
                  Frame <span className="font-mono font-bold text-dam-text">{currentFrameIdx}</span> of{' '}
                  <span className="font-mono font-bold text-dam-text">{frameList.length - 1}</span>
                </span>
                {frameList[currentFrameIdx] && (
                  <span className="text-dam-muted">
                    {new Date((frameList[currentFrameIdx]?.log_time_ns ?? 0) / 1_000_000).toLocaleTimeString()}
                  </span>
                )}
              </div>

              {/* Frame navigation */}
              <div className="p-3 border-t border-dam-border/30 flex items-center gap-2 bg-dam-surface-1">
                <button
                  onClick={() => {
                    // Find previous frame with same camera
                    const prevIdx = currentFrameIdx > 0 ? currentFrameIdx - 1 : frameList.length - 1
                    // In a real implementation, you'd need to update the cycle selection
                  }}
                  className="p-1 text-dam-muted hover:text-dam-text transition-colors disabled:opacity-30"
                  disabled={currentFrameIdx === 0}
                >
                  <ChevronLeft size={14} />
                </button>
                <div className="flex-1 h-1 bg-dam-surface-2 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-dam-blue"
                    style={{
                      width: `${frameList.length > 1 ? (currentFrameIdx / (frameList.length - 1)) * 100 : 0}%`,
                    }}
                  />
                </div>
                <button
                  onClick={() => {
                    // Find next frame
                    const nextIdx = currentFrameIdx < frameList.length - 1 ? currentFrameIdx + 1 : 0
                    // In a real implementation, you'd need to update the cycle selection
                  }}
                  className="p-1 text-dam-muted hover:text-dam-text transition-colors disabled:opacity-30"
                  disabled={currentFrameIdx === frameList.length - 1}
                >
                  <ChevronRight size={14} />
                </button>
              </div>
            </>
          ) : (
            <div className="flex items-center justify-center h-64 text-dam-muted">
              <p className="text-sm">No frames available for this camera</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
