export const PICK_STATUS_COLORS = {
  active: 'bg-blue-100 text-blue-800',
  resolved_win: 'bg-green-100 text-green-800',
  resolved_loss: 'bg-red-100 text-red-800',
  cashed_out: 'bg-slate-200 text-slate-700',
}

// Covers every flight_instances.status enum value (spec section 2), plus
// the API's synthetic "not_scheduled_yet" (no flight_instance exists yet
// for today — not a real status, see src/api/flights.py).
export const FLIGHT_STATUS_COLORS = {
  not_scheduled_yet: 'bg-slate-50 text-slate-400',
  scheduled: 'bg-slate-100 text-slate-700',
  boarding: 'bg-indigo-100 text-indigo-800',
  departed: 'bg-blue-100 text-blue-800',
  airborne: 'bg-sky-100 text-sky-800',
  landed: 'bg-emerald-100 text-emerald-800',
  delayed: 'bg-amber-100 text-amber-800',
  diverted: 'bg-orange-100 text-orange-800',
  cancelled: 'bg-red-100 text-red-800',
}

/** Shared status badge, used on both the wallet view (pick status / flight
 * status) and the flight pool browse page (flight status), so styling
 * stays consistent across both. `label` is optional — omit it for a
 * bare badge with no small caption above it. */
export function StatusBadge({ label, value, colors, className = '' }) {
  return (
    <div className={`leading-tight ${className}`}>
      {label && <div className="text-[10px] text-slate-400 uppercase tracking-wide">{label}</div>}
      <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${colors[value] ?? 'bg-slate-100 text-slate-700'}`}>
        {value}
      </span>
    </div>
  )
}
