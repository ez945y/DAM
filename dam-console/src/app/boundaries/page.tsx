import { BoundaryList } from '@/components/BoundaryList'

export default function BoundariesPage() {
  return (
    <div className="p-4 space-y-4">
      <h1 className="text-dam-text font-bold text-lg tracking-wide">Boundaries</h1>
      <p className="text-dam-muted text-xs">
        Manage boundary container configurations. Changes here update the in-memory config store
        (use the Stackfile for persistent configuration).
      </p>
      <BoundaryList />
    </div>
  )
}
