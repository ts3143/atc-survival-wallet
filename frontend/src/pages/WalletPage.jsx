import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getWallet, getWalletPickEvents } from '../api.js'

const POLL_INTERVAL_MS = 5000

const STATUS_COLORS = {
  active: 'bg-blue-100 text-blue-800',
  resolved_win: 'bg-green-100 text-green-800',
  resolved_loss: 'bg-red-100 text-red-800',
  cashed_out: 'bg-slate-200 text-slate-700',
}

function formatMoney(value) {
  return value == null ? '—' : `$${Number(value).toFixed(2)}`
}

function StatusBadge({ status }) {
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_COLORS[status] ?? 'bg-slate-100'}`}>
      {status}
    </span>
  )
}

function PickEvents({ pickId }) {
  const { data: events, isLoading, error } = useQuery({
    queryKey: ['wallet-pick-events', pickId],
    queryFn: () => getWalletPickEvents(pickId),
    refetchInterval: POLL_INTERVAL_MS,
  })

  if (isLoading) return <p className="text-xs text-slate-500 px-3 py-2">Loading events…</p>
  if (error) return <p className="text-xs text-red-600 px-3 py-2">Failed to load events: {error.message}</p>
  if (!events?.length) return <p className="text-xs text-slate-500 px-3 py-2">No wallet_events yet — waiting on the next poller tick.</p>

  return (
    <table className="w-full text-xs">
      <thead className="text-slate-500">
        <tr>
          <th className="text-left px-3 py-1">Occurred</th>
          <th className="text-left px-3 py-1">Event</th>
          <th className="text-right px-3 py-1">Amount</th>
          <th className="text-left px-3 py-1">Metadata</th>
        </tr>
      </thead>
      <tbody>
        {events.map((ev) => (
          <tr key={ev.id} className="border-t border-slate-100">
            <td className="px-3 py-1 whitespace-nowrap">{new Date(ev.occurred_at).toLocaleTimeString()}</td>
            <td className="px-3 py-1 font-mono">{ev.event_type}</td>
            <td className={`px-3 py-1 text-right font-mono ${Number(ev.amount) < 0 ? 'text-red-600' : 'text-green-700'}`}>
              {Number(ev.amount) >= 0 ? '+' : ''}
              {formatMoney(ev.amount)}
            </td>
            <td className="px-3 py-1 text-slate-500 font-mono">{JSON.stringify(ev.event_metadata)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function PickRow({ pick }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="border border-slate-200 rounded bg-white">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center justify-between px-3 py-2 text-left"
      >
        <div>
          <span className="font-medium">
            {pick.flight.carrier_code}
            {pick.flight.flight_number}
          </span>{' '}
          <span className="text-slate-600">
            {pick.flight.origin_airport} → {pick.flight.dest_airport}
          </span>
          <div className="text-xs text-slate-500">
            flight status: <span className="font-mono">{pick.flight_instance.status}</span> · stake{' '}
            {formatMoney(pick.staked_amount)}
            {pick.resolved_amount != null && <> · resolved amount {formatMoney(pick.resolved_amount)}</>}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={pick.status} />
          <span className="text-slate-400 text-xs">{expanded ? '▲' : '▼'}</span>
        </div>
      </button>
      {expanded && (
        <div className="border-t border-slate-100">
          <PickEvents pickId={pick.id} />
        </div>
      )}
    </div>
  )
}

export default function WalletPage() {
  const { data: wallet, isLoading, error } = useQuery({
    queryKey: ['wallet'],
    queryFn: getWallet,
    refetchInterval: POLL_INTERVAL_MS,
  })

  if (isLoading) return <p>Loading wallet…</p>
  if (error) return <p className="text-red-600">Failed to load wallet: {error.message}</p>

  const activePicks = wallet.picks.filter((p) => p.status === 'active')
  const resolvedPicks = wallet.picks.filter((p) => p.status !== 'active')

  return (
    <div>
      <div className="flex items-baseline gap-3 mb-6">
        <h1 className="text-xl font-semibold">Wallet</h1>
        <span className="text-2xl font-mono">{formatMoney(wallet.balance)}</span>
        <span className="text-xs text-slate-400">auto-refreshing every {POLL_INTERVAL_MS / 1000}s</span>
      </div>

      <h2 className="text-sm font-semibold text-slate-700 mb-2">Active picks ({activePicks.length})</h2>
      <div className="space-y-2 mb-6">
        {activePicks.length === 0 && <p className="text-sm text-slate-500">No active picks yet — draft a flight from the pool.</p>}
        {activePicks.map((pick) => (
          <PickRow key={pick.id} pick={pick} />
        ))}
      </div>

      {resolvedPicks.length > 0 && (
        <>
          <h2 className="text-sm font-semibold text-slate-700 mb-2">Resolved / cashed out ({resolvedPicks.length})</h2>
          <div className="space-y-2">
            {resolvedPicks.map((pick) => (
              <PickRow key={pick.id} pick={pick} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}
