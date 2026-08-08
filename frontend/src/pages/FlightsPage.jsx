import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { listFlights } from '../api.js'
import { AirportRoute } from '../components/Airport.jsx'
import { StatusBadge, FLIGHT_STATUS_COLORS } from '../components/StatusBadge.jsx'
import { formatLocalDateTime } from '../dateFormat.js'

const POLL_INTERVAL_MS = 5000

const SORT_OPTIONS = [
  { value: '', label: 'Carrier / flight number' },
  { value: 'scheduled_dep_utc', label: 'Departure time (UTC)' },
  { value: 'on_time_pct', label: 'On-time %' },
  { value: 'delay_stddev', label: 'Volatility (delay stddev)' },
  { value: 'cancellation_pct', label: 'Cancellation %' },
]

function formatPct(value) {
  return value == null ? '—' : `${Number(value).toFixed(1)}%`
}

function formatTime(value) {
  return value ? value.slice(0, 5) : '—'
}

export default function FlightsPage() {
  const [sortBy, setSortBy] = useState('')
  const [order, setOrder] = useState('asc')
  const [carrierCode, setCarrierCode] = useState('')

  const { data: flights, isLoading, error } = useQuery({
    queryKey: ['flights', sortBy, order, carrierCode],
    queryFn: () => listFlights({ sortBy: sortBy || undefined, order, carrierCode: carrierCode || undefined }),
    refetchInterval: POLL_INTERVAL_MS,
  })

  if (isLoading) return <p>Loading flight pool…</p>
  if (error) return <p className="text-red-600">Failed to load flights: {error.message}</p>

  const carriers = [...new Set((flights ?? []).map((f) => f.carrier_code))].sort()
  const now = Date.now()
  const isPastDeparture = (f) => f.scheduled_dep_utc && new Date(f.scheduled_dep_utc).getTime() < now

  return (
    <div>
      <div className="flex items-baseline gap-3 mb-4">
        <h1 className="text-xl font-semibold">Flight Pool ({flights.length})</h1>
        <span className="text-xs text-slate-400">auto-refreshing every {POLL_INTERVAL_MS / 1000}s</span>
      </div>

      <div className="flex flex-wrap gap-3 mb-4 text-sm">
        <label className="flex items-center gap-2">
          Sort by
          <select
            className="border border-slate-300 rounded px-2 py-1"
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
          >
            {SORT_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2">
          Order
          <select
            className="border border-slate-300 rounded px-2 py-1"
            value={order}
            onChange={(e) => setOrder(e.target.value)}
          >
            <option value="asc">Ascending</option>
            <option value="desc">Descending</option>
          </select>
        </label>
        <label className="flex items-center gap-2">
          Carrier
          <select
            className="border border-slate-300 rounded px-2 py-1"
            value={carrierCode}
            onChange={(e) => setCarrierCode(e.target.value)}
          >
            <option value="">All</option>
            {carriers.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="overflow-x-auto border border-slate-200 rounded bg-white">
        <table className="w-full text-sm text-left">
          <thead className="bg-slate-100 text-slate-600">
            <tr>
              <th className="px-3 py-2">Flight</th>
              <th className="px-3 py-2">Route</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2">Typical Dep / Arr</th>
              <th className="px-3 py-2">Today's Sched Dep / Arr</th>
              <th className="px-3 py-2">Distance</th>
              <th className="px-3 py-2">On-time %</th>
              <th className="px-3 py-2">Volatility (stddev)</th>
              <th className="px-3 py-2">Cancel %</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {flights.map((f) => {
              const past = isPastDeparture(f)
              return (
                <tr
                  key={f.id}
                  className={`border-t border-slate-100 ${past ? 'bg-amber-50/60 border-l-2 border-l-amber-400' : ''}`}
                >
                  <td className="px-3 py-2 font-medium">
                    {f.carrier_code}
                    {f.flight_number}
                  </td>
                  <td className="px-3 py-2">
                    <AirportRoute origin={f.origin_airport} dest={f.dest_airport} />
                  </td>
                  <td className="px-3 py-2">
                    <StatusBadge value={f.status} colors={FLIGHT_STATUS_COLORS} />
                  </td>
                  <td className="px-3 py-2 text-slate-600">
                    {formatTime(f.typical_dep_time)} / {formatTime(f.typical_arr_time)}
                  </td>
                  <td className="px-3 py-2">
                    <div className={past ? 'text-amber-700 font-medium' : 'text-slate-600'}>
                      {formatLocalDateTime(f.scheduled_dep_utc, f.origin_timezone)}
                      {past && (
                        <span className="ml-1.5 text-[10px] uppercase tracking-wide text-amber-600 align-middle">
                          past
                        </span>
                      )}
                    </div>
                    <div className="text-slate-500">
                      {formatLocalDateTime(f.scheduled_arr_utc, f.dest_timezone)}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-slate-600">{f.distance_bucket}</td>
                  <td className="px-3 py-2">{formatPct(f.on_time_pct)}</td>
                  <td className="px-3 py-2">{f.delay_stddev ?? '—'}</td>
                  <td className="px-3 py-2">{formatPct(f.cancellation_pct)}</td>
                  <td className="px-3 py-2">
                    <Link to={`/draft/${f.id}`} className="text-blue-600 hover:underline">
                      Draft
                    </Link>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
