/**
 * Shows the time in the given airport's local timezone with a short tz
 * abbreviation (e.g. "Aug 4 18:00 EDT"), like a flight-tracking app would
 * (departure in origin-local time, arrival in destination-local time) —
 * timeZone comes from the backend's src/lib/airport_timezones table (see
 * schemas.FlightListItem/PickFlightSummary origin_timezone/dest_timezone),
 * not duplicated here. Falls back to labeled UTC if we don't have a
 * timezone for some reason.
 */
export function formatLocalDateTime(value, timeZone) {
  if (!value) return '—'
  const d = new Date(value)
  if (!timeZone) {
    return `${d.toLocaleString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })} ${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')} UTC`
  }
  const formatted = new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone,
  }).format(d)
  const tzAbbr = new Intl.DateTimeFormat('en-US', { timeZoneName: 'short', timeZone })
    .formatToParts(d)
    .find((p) => p.type === 'timeZoneName')?.value
  return tzAbbr ? `${formatted} ${tzAbbr}` : formatted
}

/**
 * Rough countdown to/past a target ISO timestamp, e.g. { label: "2h 14m",
 * overdue: false }. Only "rough" (rounded to the minute) — this is a
 * debugging aid, not a precise ETA (the target itself is just
 * scheduled_dep_utc/scheduled_arr_utc, not adjusted for any delay already
 * observed, so a flight running late will still count down toward its
 * original scheduled time going negative, not a re-estimated one).
 */
export function formatCountdown(targetIso, now = Date.now()) {
  if (!targetIso) return null
  const diffMs = new Date(targetIso).getTime() - now
  const overdue = diffMs < 0
  const totalMinutes = Math.round(Math.abs(diffMs) / 60000)
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  const label = hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`
  return { label, overdue }
}
