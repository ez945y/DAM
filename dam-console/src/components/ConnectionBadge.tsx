import { Terminal } from 'lucide-react'

interface Props {
  readonly connected: boolean
}

export function ConnectionBadge({ connected }: Props) {
  return (
    <div className="flex flex-col items-end gap-1">
      <div className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-semibold border transition-all ${
        connected
          ? 'bg-green-950/50 text-dam-green border-green-900 shadow-[0_0_8px_rgba(34,197,94,0.15)]'
          : 'bg-dam-surface-2 text-dam-muted border-dam-border'
      }`}>
        <span className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-dam-green animate-pulse' : 'bg-dam-muted'}`} />
        {connected ? 'Live' : 'Offline'}
      </div>
      {!connected && (
        <div className="flex items-center gap-1 text-[10px] text-dam-muted/70">
          <Terminal size={9} />
          <code className="font-mono">python scripts/dev_server.py</code>
        </div>
      )}
    </div>
  )
}
