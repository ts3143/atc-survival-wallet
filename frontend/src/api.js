const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8010'

async function request(path, options = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail || detail
    } catch {
      // ignore, use statusText
    }
    throw new Error(detail)
  }
  if (res.status === 204) return null
  return res.json()
}

export function listFlights({ sortBy, order, carrierCode } = {}) {
  const params = new URLSearchParams()
  if (sortBy) params.set('sort_by', sortBy)
  if (order) params.set('order', order)
  if (carrierCode) params.set('carrier_code', carrierCode)
  const qs = params.toString()
  return request(`/api/flights${qs ? `?${qs}` : ''}`)
}

export function getFlight(flightId) {
  return request(`/api/flights/${flightId}`)
}

export function getWallet() {
  return request('/api/wallet')
}

export function createWalletPick({ flightDefinitionId, stakedAmount }) {
  return request('/api/wallet-picks', {
    method: 'POST',
    body: JSON.stringify({
      flight_definition_id: flightDefinitionId,
      staked_amount: stakedAmount,
    }),
  })
}

export function getWalletPickEvents(pickId) {
  return request(`/api/wallet-picks/${pickId}/events`)
}
