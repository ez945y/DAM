export function StatsCard({
  label,
  value,
  sub,
  accent = false,
  icon,
}: {
  label: string
  value: string | number
  sub?: string
  accent?: boolean
  icon?: React.ReactNode
}) {
  return (
    <div className="card-accent-top relative panel px-4 py-3.5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="section-label mb-1">{label}</p>
          <p className={`metric-value text-2xl leading-none ${accent ? 'text-dam-blue' : 'text-dam-text'}`}>
            {value}
          </p>
          {sub && <p className="text-dam-muted text-[11px] mt-1">{sub}</p>}
        </div>
        {icon && (
          <div className="shrink-0 text-dam-muted opacity-30 mt-0.5">{icon}</div>
        )}
      </div>
    </div>
  )
}
