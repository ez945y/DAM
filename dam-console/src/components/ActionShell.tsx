'use client'
import React, { useState } from 'react'
import {
  Download, Upload, Check, ShieldCheck,
  RotateCcw, ChevronDown, Copy
} from 'lucide-react'

interface ActionShellProps {
  readonly title: string
  readonly description: string
  readonly restarting?: boolean
  readonly restartOk?: boolean
  readonly restartError?: string | null
  readonly saved?: boolean
  readonly yaml: string
  readonly onYamlChange?: (v: string) => void
  readonly onApply?: () => void
  readonly onImport?: () => void
  readonly onExport?: () => void
  readonly children: React.ReactNode
}

export function ActionShell({
  title,
  description,
  restarting,
  restartOk,
  restartError,
  saved,
  yaml,
  onYamlChange,
  onApply,
  onImport,
  onExport,
  children
}: ActionShellProps) {
  const [yamlOpen, setYamlOpen] = useState(true)
  const [copied, setCopied] = useState(false)
  const applyLabel = restarting ? 'Syncing...' : restartOk ? 'Applied' : 'Apply & Restart'

  const handleCopy = async () => {
    await navigator.clipboard.writeText(yaml)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="min-h-screen max-w-6xl mx-auto animate-in fade-in duration-700">
      {/* Configuration Header (Sticky) */}
      <div className="sticky top-0 z-50 bg-dam-bg/80 backdrop-blur-md px-8 pt-8 pb-6 border-b border-dam-border/40 mb-8">
        <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-black text-dam-text tracking-tighter uppercase italic leading-none">{title}</h1>
            <p className="text-dam-muted text-[10px] font-bold uppercase tracking-[0.3em] mt-2 opacity-60">{description}</p>
          </div>

          <div className="flex items-center gap-2">
            {saved && (
              <span className="flex items-center gap-1 text-dam-green text-[10px] font-bold tracking-widest uppercase mr-2 animate-in fade-in duration-500">
                <Check size={11} /> Saved
              </span>
            )}
            {onImport && (
              <button
                onClick={onImport}
                className="flex items-center gap-1.5 px-4 py-2 bg-dam-surface-2 border border-dam-border text-dam-muted text-xs rounded hover:text-dam-text transition-all hover:bg-dam-surface-3"
              >
                <Upload size={12} /> Import
              </button>
            )}
            {onExport && (
              <button
                onClick={onExport}
                className="flex items-center gap-1.5 px-4 py-2 bg-dam-surface-2 border border-dam-border text-dam-muted text-xs rounded hover:text-dam-text transition-all hover:bg-dam-surface-3"
              >
                <Download size={12} /> Export
              </button>
            )}
            {onApply && (
              <button
                onClick={onApply}
                disabled={restarting}
                className="flex items-center gap-1.5 px-5 py-2 bg-dam-blue text-white text-xs font-black rounded hover:bg-dam-blue-bright transition-all disabled:opacity-50 ml-2 shadow-xl shadow-dam-blue/20 uppercase tracking-widest"
              >
                {restarting
                  ? <RotateCcw size={12} className="animate-spin" />
                  : restartOk
                    ? <Check size={12} />
                    : <ShieldCheck size={12} />
                }
                {applyLabel}
              </button>
            )}
          </div>
        </div>
      </div>

      {restartError && (
        <div className="p-4 bg-dam-red/10 border border-dam-red/30 rounded-xl text-dam-red text-[11px] font-mono whitespace-pre-wrap animate-in slide-in-from-top-2 duration-300">
          <div className="flex items-center gap-2 mb-1 font-bold text-[10px] uppercase">
             <span>Restart Failed</span>
          </div>
          {restartError}
        </div>
      )}

      {/* Page Content */}
      <div className="px-8 pb-10 space-y-6">
        {children}
      </div>

      {/* YAML Section (Shared between Guard & Config) */}
      <div className="mt-12 pt-12 border-t border-dam-border/40 px-8">
        <button
          type="button"
          className="flex items-center justify-between cursor-pointer group mb-5 w-full text-left"
          onClick={() => setYamlOpen(!yamlOpen)}
        >
          <div className="flex items-center gap-3">
            <h2 className="text-dam-muted text-[11px] uppercase tracking-[0.4em] font-black">Stackfile YAML Preview</h2>
            <div className={`transition-all duration-300 ${yamlOpen ? 'rotate-180 text-dam-blue' : 'text-dam-muted/40'}`}>
              <ChevronDown size={14} />
            </div>
          </div>
          {yamlOpen && (
            <button
              onClick={(e) => { e.stopPropagation(); handleCopy(); }}
              className="flex items-center gap-1.5 px-3 py-1 rounded bg-dam-surface-3 border border-dam-border text-[10px] font-bold text-dam-muted hover:text-dam-text transition-colors"
            >
              {copied ? <Check size={10} className="text-dam-green" /> : <Copy size={10} />}
              {copied ? 'COPIED' : 'COPY'}
            </button>
          )}
        </button>

        {yamlOpen && (
          <div className="glass-card mt-2 group animate-in zoom-in-95 duration-300">
            <textarea
              readOnly={!onYamlChange}
              value={yaml}
              onChange={(e) => onYamlChange?.(e.target.value)}
              spellCheck={false}
              className="w-full h-[400px] bg-transparent p-6 text-[11px] font-mono text-dam-text/80 focus:outline-none transition-all leading-relaxed scrollbar-none relative z-10"
            />
            {!onYamlChange && (
              <div className="absolute top-4 right-4 px-2 py-0.5 rounded bg-dam-surface-3/80 border border-dam-border/40 text-[9px] text-dam-muted/60 font-black tracking-widest uppercase z-20">
                Read Only Mode
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
