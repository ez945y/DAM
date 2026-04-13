import { RiskLogTable } from '@/components/RiskLogTable'
import { PageShell } from '@/components/PageShell'

export default function RiskLogPage() {
  return (
    <PageShell 
      title="Risk Log" 
      subtitle="Historical safety event auditing & statistics"
    >
      <RiskLogTable />
    </PageShell>
  )
}
