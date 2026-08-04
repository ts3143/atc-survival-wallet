import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { createWalletPick, getFlight } from '../api.js'

export default function DraftPage() {
  const { flightId } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [stake, setStake] = useState('50')
  const [formError, setFormError] = useState(null)

  const { data: flight, isLoading, error } = useQuery({
    queryKey: ['flight', flightId],
    queryFn: () => getFlight(flightId),
  })

  const mutation = useMutation({
    mutationFn: () => createWalletPick({ flightDefinitionId: flightId, stakedAmount: Number(stake) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['wallet'] })
      navigate('/wallet')
    },
    onError: (err) => setFormError(err.message),
  })

  if (isLoading) return <p>Loading flight…</p>
  if (error) return <p className="text-red-600">Failed to load flight: {error.message}</p>

  const handleSubmit = (e) => {
    e.preventDefault()
    setFormError(null)
    const amount = Number(stake)
    if (!amount || amount <= 0) {
      setFormError('Stake must be a positive number')
      return
    }
    mutation.mutate()
  }

  return (
    <div className="max-w-md">
      <Link to="/" className="text-sm text-blue-600 hover:underline">
        ← Back to flight pool
      </Link>

      <h1 className="text-xl font-semibold mt-2 mb-1">
        {flight.carrier_code}
        {flight.flight_number} — {flight.origin_airport} → {flight.dest_airport}
      </h1>
      <p className="text-sm text-slate-600 mb-4">
        Typical departure {flight.typical_dep_time?.slice(0, 5)} local · On-time{' '}
        {flight.on_time_pct != null ? `${Number(flight.on_time_pct).toFixed(1)}%` : '—'} · Volatility{' '}
        {flight.delay_stddev ?? '—'} min stddev · Distance: {flight.distance_bucket}
      </p>

      {flight.current_instance ? (
        <p className="text-sm text-slate-600 mb-4">
          Today's instance ({flight.current_instance.flight_date}): status{' '}
          <span className="font-mono">{flight.current_instance.status}</span>
        </p>
      ) : (
        <p className="text-sm text-amber-600 mb-4">
          No flight_instance exists yet for this flight — drafting will fail until the schedule refresher runs.
        </p>
      )}

      <form onSubmit={handleSubmit} className="border border-slate-200 rounded bg-white p-4">
        <label className="block text-sm font-medium mb-1" htmlFor="stake">
          Stake amount
        </label>
        <input
          id="stake"
          type="number"
          min="1"
          step="1"
          className="border border-slate-300 rounded px-2 py-1 w-full mb-3"
          value={stake}
          onChange={(e) => setStake(e.target.value)}
        />

        {formError && <p className="text-red-600 text-sm mb-3">{formError}</p>}

        <button
          type="submit"
          disabled={mutation.isPending}
          className="bg-slate-900 text-white rounded px-4 py-2 disabled:opacity-50"
        >
          {mutation.isPending ? 'Drafting…' : 'Draft this flight'}
        </button>
      </form>
    </div>
  )
}
