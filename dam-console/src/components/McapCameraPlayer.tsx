'use client'
import React, { useEffect, useState, useRef, useCallback } from 'react'
import { api } from '@/lib/api'
import type { McapFrame } from '@/lib/api'
import {
  ChevronLeft, ChevronRight, Play, Pause,
  Camera, Loader2, ImageOff, LayoutGrid, Maximize2, Radio,
} from 'lucide-react'

interface McapCameraPlayerProps {
  filename: string
  cameras: string[]
  /** log_time_ns of the currently selected cycle — used to sync the player. */
  currentTimestampNs: number | null
  /**
   * these instead of fetching frames from the MCAP API, and the scrubbar +
   * playback controls will be hidden.
   */
  liveImages?: Record<string, string | Blob> | null
  /** Whether live mode is currently enabled (shows badge, hides controls). */
  liveMode?: boolean
}

/** Binary search: index of frame whose timestamp_ns is closest to target. */
function nearestFrameIdx(frames: McapFrame[], targetNs: number): number {
  if (frames.length === 0) return 0
  let lo = 0
  let hi = frames.length - 1
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (frames[mid].log_time_ns < targetNs) lo = mid + 1
    else hi = mid
  }
  if (lo > 0) {
    const dLo = Math.abs(frames[lo].log_time_ns - targetNs)
    const dPrev = Math.abs(frames[lo - 1].log_time_ns - targetNs)
    if (dPrev < dLo) return lo - 1
  }
  return lo
}

// ── Single camera cell (MCAP-backed) ──────────────────────────────────────────

function CameraCell({
  filename,
  cam,
  frameIdx,
  tsNs,
  label,
  compact = false,
}: {
  filename: string
  cam: string
  frameIdx: number
  tsNs?: number
  label?: string
  compact?: boolean
}) {
  const [error, setError] = useState(false)
  const [loadedUrl, setLoadedUrl] = useState<string | null>(null)
  const url = tsNs !== undefined
    ? api.mcapFrameAtUrl(filename, cam, tsNs)
    : api.mcapFrameUrl(filename, cam, frameIdx)

  // Preload next frame to avoid black flash during playback
  const nextUrl = tsNs !== undefined
    ? null
    : api.mcapFrameUrl(filename, cam, frameIdx + 1)

  useEffect(() => {
    setError(false)
  }, [url])

  useEffect(() => {
    if (nextUrl) {
      const img = new Image()
      img.src = nextUrl
    }
  }, [nextUrl])

  const handleLoad = () => {
    setLoadedUrl(url)
    setError(false)
  }

  return (
    <div className="relative bg-black flex items-center justify-center h-full w-full overflow-hidden">
      {error ? (
        <div className="flex flex-col items-center gap-1 text-dam-muted/40">
          <ImageOff size={compact ? 18 : 24} />
          {!compact && <span className="text-[10px]">{cam}</span>}
        </div>
      ) : (
        <>
          {loadedUrl && loadedUrl !== url && (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={loadedUrl}
              alt={`${cam} frame cache`}
              className="max-w-full max-h-full object-contain absolute"
            />
          )}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={url}
            alt={`${cam} frame ${frameIdx}`}
            className="max-w-full max-h-full object-contain relative z-10"
            onLoad={handleLoad}
            onError={() => setError(true)}
          />
        </>
      )}
      {label && (
        <div className="absolute top-1.5 left-1.5 bg-black/60 text-white text-[9px] font-mono px-1.5 py-0.5 rounded z-20">
          {label}
        </div>
      )}
    </div>
  )
}

// ── Live camera cell (WS-backed) ──────────────────────────────────────────────

function LiveCameraCell({
  cam,
  src,
  label,
}: {
  cam: string
  src: string | Blob | null | undefined
  label?: string
}) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null)

  // Manage ObjectURL lifecycle for binary image blobs
  useEffect(() => {
    if (src instanceof Blob) {
      const url = URL.createObjectURL(src)
      setObjectUrl(url)
      return () => URL.revokeObjectURL(url)
    } else {
      setObjectUrl(null)
    }
  }, [src])

  const displaySrc = src instanceof Blob ? objectUrl : src

  return (
    <div className="relative bg-black flex items-center justify-center h-full w-full overflow-hidden">
      {displaySrc ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={displaySrc as string}
          alt={`${cam} live`}
          className="max-w-full max-h-full object-contain"
        />
      ) : (
        <div className="flex flex-col items-center gap-1 text-dam-muted/40">
          <Radio size={20} className="animate-pulse" />
          <span className="text-[10px]">Waiting for frames…</span>
        </div>
      )}
      {(label || cam) && (
        <div className="absolute top-1.5 left-1.5 bg-black/60 text-white text-[9px] font-mono px-1.5 py-0.5 rounded z-20 flex items-center gap-1">
          <Radio size={7} className="animate-pulse text-red-400" />
          {label ?? cam}
        </div>
      )}
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

export function McapCameraPlayer({
  filename,
  cameras,
  currentTimestampNs,
  liveImages,
  liveMode = false,
}: McapCameraPlayerProps) {
  const [selectedCam, setSelectedCam] = useState<string | null>(null)
  // frames indexed by camera name
  const [framesMap, setFramesMap] = useState<Record<string, McapFrame[]>>({})
  const [loadingCams, setLoadingCams] = useState<Set<string>>(new Set())
  const [frameIdx, setFrameIdx] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [gridMode, setGridMode] = useState(false)
  const playIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // When live mode is enabled, stop playback
  useEffect(() => {
    if (liveMode) {
      setPlaying(false)
    }
  }, [liveMode])

  // Auto-select first camera; enable grid when >1 camera
  useEffect(() => {
    if (cameras.length > 0 && !selectedCam) {
      setSelectedCam(cameras[0])
    }
    if (cameras.length > 1) {
      setGridMode(true)
    }
  }, [cameras, selectedCam])

  // Load frame list for a camera (lazy, cached in framesMap)
  const loadCam = useCallback((cam: string) => {
    // In live mode, don't load frames from MCAP
    if (liveMode) return
    if (framesMap[cam] !== undefined) return
    setLoadingCams(prev => new Set([...prev, cam]))
    api.listMcapFrames(filename, cam)
      .then(data => {
        setFramesMap(prev => ({ ...prev, [cam]: data.frames ?? [] }))
      })
      .catch(() => {
        setFramesMap(prev => ({ ...prev, [cam]: [] }))
      })
      .finally(() => {
        setLoadingCams(prev => { const s = new Set(prev); s.delete(cam); return s })
      })
  }, [filename, framesMap, liveMode])

  // Load all cameras for grid mode; load selected camera for single mode
  useEffect(() => {
    if (liveMode) return
    if (gridMode) {
      cameras.forEach(loadCam)
    } else if (selectedCam) {
      loadCam(selectedCam)
    }
  }, [gridMode, selectedCam, cameras, loadCam, liveMode])

  // Sync frameIdx to cycle timestamp (use first available camera's frame list)
  useEffect(() => {
    if (liveMode || currentTimestampNs == null) return
    const refFrames = selectedCam ? framesMap[selectedCam] : Object.values(framesMap)[0]
    if (!refFrames || refFrames.length === 0) return
    setFrameIdx(nearestFrameIdx(refFrames, currentTimestampNs))
  }, [currentTimestampNs, framesMap, selectedCam, liveMode])

  // Playback interval
  useEffect(() => {
    if (playIntervalRef.current) clearInterval(playIntervalRef.current)
    if (!playing || liveMode) return
    const refFrames = selectedCam ? framesMap[selectedCam] : Object.values(framesMap)[0]
    const total = refFrames?.length ?? 0
    if (total === 0) return
    playIntervalRef.current = setInterval(() => {
      setFrameIdx(prev => {
        if (prev >= total - 1) { setPlaying(false); return prev }
        return prev + 1
      })
    }, 100)
    return () => { if (playIntervalRef.current) clearInterval(playIntervalRef.current) }
  }, [playing, framesMap, selectedCam, liveMode])

  const refFrames = selectedCam ? framesMap[selectedCam] : Object.values(framesMap)[0] ?? []
  const totalFrames = refFrames?.length ?? 0

  const goFrame = useCallback((dir: 'prev' | 'next') => {
    setPlaying(false)
    setFrameIdx(prev =>
      dir === 'prev' ? Math.max(0, prev - 1) : Math.min(totalFrames - 1, prev + 1)
    )
  }, [totalFrames])

  const handleSlider = (e: React.ChangeEvent<HTMLInputElement>) => {
    setPlaying(false)
    setFrameIdx(Number(e.target.value))
  }

  // Derive live camera list: use liveImages keys if in live mode, else cameras prop
  const liveCamList = liveMode
    ? (liveImages ? Object.keys(liveImages) : cameras)
    : cameras

  if (!liveMode && cameras.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-2 text-dam-muted py-8">
        <Camera size={28} className="opacity-30" />
        <p className="text-sm">No camera footage in this session</p>
        <p className="text-xs opacity-60">Images are captured on REJECT / CLAMP events</p>
      </div>
    )
  }

  if (liveMode && liveCamList.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-2 text-dam-muted py-8">
        <Radio size={28} className="opacity-30 animate-pulse" />
        <p className="text-sm">Waiting for live camera data…</p>
        <p className="text-xs opacity-60">Camera frames arrive with each cycle event</p>
      </div>
    )
  }

  const isLoading = !liveMode && cameras.some(c => loadingCams.has(c))
  const currentFrame = !liveMode ? refFrames?.[frameIdx] : null
  const timeOffsetMs = !liveMode && currentTimestampNs != null && currentFrame
    ? (currentFrame.log_time_ns - currentTimestampNs) / 1_000_000
    : null

  const handleSelectCamera = (cam: string) => {
    setSelectedCam(cam)
    setGridMode(false)
    setPlaying(false)
  }

  const currentCams = liveMode ? liveCamList : cameras
  const showGrid = gridMode || currentCams.length > 1

  return (
    <div className="flex flex-col h-full bg-dam-surface-1 border border-dam-border rounded-lg overflow-hidden">
      {/* Header: camera tabs + grid toggle */}
      <div className="flex items-center gap-1.5 px-3 py-2 border-b border-dam-border/50 bg-dam-surface-2/50 shrink-0">
        {liveMode ? (
          <Radio size={12} className="text-red-400 animate-pulse shrink-0" />
        ) : (
          <Camera size={12} className="text-dam-muted shrink-0" />
        )}
        <div className="flex gap-1 flex-1 flex-wrap min-w-0">
          {currentCams.map(cam => (
            <button
              key={cam}
              onClick={() => handleSelectCamera(cam)}
              className={`px-2 py-0.5 text-[10px] font-medium rounded border transition-all ${
                !showGrid && selectedCam === cam
                  ? 'bg-dam-blue/20 text-dam-blue border-dam-blue/40'
                  : 'bg-dam-surface-1 text-dam-muted border-dam-border hover:border-dam-blue/30'
              }`}
            >
              {cam}
            </button>
          ))}
        </div>

        {/* Frame counter (MCAP mode only) */}
        {!liveMode && totalFrames > 0 && (
          <span className="text-[10px] text-dam-muted/60 font-mono shrink-0">
            {frameIdx + 1}/{totalFrames}
          </span>
        )}

        {/* Grid / single toggle */}
        {currentCams.length > 1 && (
          <button
            onClick={() => setGridMode(v => !v)}
            title={showGrid ? 'Single camera' : 'Grid view'}
            className={`p-1 rounded border transition-all ${
              showGrid
                ? 'bg-dam-blue/20 text-dam-blue border-dam-blue/40'
                : 'bg-dam-surface-1 text-dam-muted border-dam-border hover:border-dam-blue/30'
            }`}
          >
            {showGrid ? <Maximize2 size={11} /> : <LayoutGrid size={11} />}
          </button>
        )}
      </div>

      {/* Image area */}
      <div className="flex-1 min-h-0 relative overflow-hidden">
        {!liveMode && isLoading && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/60 z-10">
            <Loader2 size={20} className="animate-spin text-dam-muted" />
          </div>
        )}

        {showGrid ? (
          // ── Grid view ─────────────────────────────────────────────────────
          <div
            className={`h-full grid gap-0.5 bg-dam-border/20 ${
              currentCams.length === 1 ? 'grid-cols-1' :
              currentCams.length === 2 ? 'grid-cols-2' :
              currentCams.length <= 4 ? 'grid-cols-2' :
              'grid-cols-3'
            }`}
          >
            {currentCams.map(cam => {
              if (liveMode) {
                return (
                  <div key={cam} className="relative bg-black overflow-hidden" style={{ minHeight: 0 }}>
                    <LiveCameraCell cam={cam} src={liveImages?.[cam]} label={currentCams.length > 1 ? cam : undefined} />
                  </div>
                )
              }
              const camFrames = framesMap[cam]
              const hasFrames = camFrames && camFrames.length > 0
              return (
                <div key={cam} className="relative bg-black overflow-hidden" style={{ minHeight: 0 }}>
                  {loadingCams.has(cam) && !currentTimestampNs ? (
                    <div className="flex items-center justify-center h-full text-dam-muted/40">
                      <Loader2 size={14} className="animate-spin" />
                    </div>
                  ) : !hasFrames && (!loadingCams.has(cam) || !currentTimestampNs) ? (
                    <div className="flex flex-col items-center justify-center h-full gap-1 text-dam-muted/40">
                      <ImageOff size={16} />
                      <span className="text-[9px]">{cam}</span>
                    </div>
                  ) : (
                    <CameraCell
                      filename={filename}
                      cam={cam}
                      frameIdx={hasFrames ? Math.min(frameIdx, camFrames.length - 1) : 0}
                      tsNs={!hasFrames && currentTimestampNs != null ? currentTimestampNs : undefined}
                      label={cam}
                      compact
                    />
                  )}
                </div>
              )
            })}
          </div>
        ) : (
          // ── Single camera view ────────────────────────────────────────────
          <div className="h-full bg-black">
            {liveMode ? (
              <LiveCameraCell cam={selectedCam ?? currentCams[0]} src={liveImages?.[selectedCam ?? currentCams[0]]} />
            ) : selectedCam && ((framesMap[selectedCam]?.length ?? 0) > 0 || (loadingCams.has(selectedCam) && currentTimestampNs != null)) ? (
              <CameraCell
                filename={filename}
                cam={selectedCam}
                frameIdx={framesMap[selectedCam]?.length ? Math.min(frameIdx, framesMap[selectedCam].length - 1) : 0}
                tsNs={!(framesMap[selectedCam]?.length) && currentTimestampNs != null ? currentTimestampNs : undefined}
              />
            ) : selectedCam && !loadingCams.has(selectedCam) ? (
              <div className="flex flex-col items-center justify-center h-full gap-2 text-dam-muted/40">
                <ImageOff size={24} />
                <span className="text-[10px]">No frames</span>
              </div>
            ) : null}
          </div>
        )}

        {/* Timestamp overlay (MCAP mode only) */}
        {!liveMode && currentFrame && !isLoading && (
          <div className="absolute bottom-2 left-2 bg-black/70 text-white text-[10px] font-mono px-2 py-0.5 rounded pointer-events-none">
            {new Date(currentFrame.log_time_ns / 1_000_000).toLocaleTimeString([], {
              hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 2,
            })}
            {timeOffsetMs != null && (
              <span className={`ml-1.5 ${Math.abs(timeOffsetMs) < 50 ? 'text-green-400' : 'text-yellow-400'}`}>
                {timeOffsetMs >= 0 ? '+' : ''}{timeOffsetMs.toFixed(0)}ms
              </span>
            )}
          </div>
        )}

        {/* Live indicator overlay */}
        {liveMode && (
          <div className="absolute bottom-2 right-2 bg-black/70 text-red-400 text-[10px] font-mono px-2 py-0.5 rounded pointer-events-none flex items-center gap-1">
            <Radio size={8} className="animate-pulse" />
            LIVE
          </div>
        )}
      </div>

      {/* Controls (MCAP mode only — hidden in live mode) */}
      {!liveMode && totalFrames > 0 && (
        <div className="border-t border-dam-border/50 bg-dam-surface-2/50 px-3 py-2 space-y-1.5 shrink-0">
          <input
            type="range"
            min={0}
            max={Math.max(0, totalFrames - 1)}
            value={frameIdx}
            onChange={handleSlider}
            className="w-full h-1.5 accent-dam-blue cursor-pointer"
          />
          <div className="flex items-center justify-center gap-2">
            <button
              onClick={() => goFrame('prev')}
              disabled={frameIdx === 0}
              className="p-1.5 rounded text-dam-muted hover:text-dam-text hover:bg-dam-surface-1 disabled:opacity-30 transition-colors"
            >
              <ChevronLeft size={14} />
            </button>
            <button
              onClick={() => setPlaying(p => !p)}
              className="px-3 py-1 rounded bg-dam-blue/20 border border-dam-blue/30 text-dam-blue hover:bg-dam-blue/30 transition-colors flex items-center gap-1.5 text-[10px] font-bold"
            >
              {playing ? <Pause size={11} /> : <Play size={11} />}
              {playing ? 'Pause' : 'Play'}
            </button>
            <button
              onClick={() => goFrame('next')}
              disabled={frameIdx === totalFrames - 1}
              className="p-1.5 rounded text-dam-muted hover:text-dam-text hover:bg-dam-surface-1 disabled:opacity-30 transition-colors"
            >
              <ChevronRight size={14} />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
