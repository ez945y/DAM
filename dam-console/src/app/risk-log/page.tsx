import { Suspense } from 'react'
import { RiskLogTable } from '@/components/RiskLogTable'
import { PageShell } from '@/components/PageShell'

export default function RiskLogPage() {
  return (
    <PageShell
      title="Risk Log"
      subtitle="Historical safety event auditing & statistics"
    >
      {/* Suspense required by useSearchParams() in RiskLogTable */}
      <Suspense fallback={<div className="text-dam-muted text-sm py-12 text-center">Loading…</div>}>
        <RiskLogTable />
      </Suspense>
    </PageShell>
  )
}
