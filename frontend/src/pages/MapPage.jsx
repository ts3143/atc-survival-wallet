import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import L from 'leaflet'
import { MapContainer, TileLayer, Marker, Popup, Polyline } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import { getCurrentPositions, getFlightTrack, getWallet } from '../api.js'
import { AirportRoute } from '../components/Airport.jsx'
import { StatusBadge, FLIGHT_STATUS_COLORS } from '../components/StatusBadge.jsx'

const POLL_INTERVAL_MS = 20000
const US_CENTER = [39.8283, -98.5795]
const US_ZOOM = 4

function metersToFeet(m) {
  return m == null ? null : Math.round(Number(m) * 3.28084)
}

function msToKnots(ms) {
  return ms == null ? null : Math.round(Number(ms) * 1.94384)
}

// Priority for color/size: selected (clicked) > inWallet (you have an
// active pick on it) > default. A selected wallet flight stays red (so
// "currently selected" is always unambiguous) but keeps the larger size.
function createPlaneIcon(heading, selected, inWallet) {
  const rotation = heading ?? 0
  const size = inWallet ? 30 : 24
  let fill = '#2563eb'
  if (inWallet) fill = '#7c3aed'
  if (selected) fill = '#dc2626'
  const half = size / 2
  const html = `<svg width="${size}" height="${size}" viewBox="0 0 24 24" style="transform: rotate(${rotation}deg); transform-origin: 12px 12px;">
      <path d="M12 1 L19 22 L12 17 L5 22 Z" fill="${fill}" stroke="white" stroke-width="1.2"/>
    </svg>`
  return L.divIcon({ html, className: '', iconSize: [size, size], iconAnchor: [half, half] })
}

function FlightTrail({ flightInstanceId }) {
  const { data: track, isLoading, error } = useQuery({
    queryKey: ['flight-track', flightInstanceId],
    queryFn: () => getFlightTrack(flightInstanceId),
  })

  if (isLoading || error || !track) return null

  const positions = track
    .filter((p) => p.latitude != null && p.longitude != null)
    .map((p) => [Number(p.latitude), Number(p.longitude)])

  if (positions.length < 2) return null

  return <Polyline positions={positions} pathOptions={{ color: '#dc2626', weight: 2, opacity: 0.7 }} />
}

function PlaneMarker({ position, selected, inWallet, onSelect, showTrail, onToggleTrail }) {
  const icon = createPlaneIcon(position.heading, selected, inWallet)
  const lat = Number(position.latitude)
  const lon = Number(position.longitude)

  return (
    <Marker
      position={[lat, lon]}
      icon={icon}
      eventHandlers={{
        click: () => onSelect(position.flight_instance_id),
        // NOT hooking popupclose -> onSelect(null) here: Leaflet closes the
        // previously-open popup as part of opening a newly-clicked one, so
        // that popupclose would fire right after the new marker's click
        // handler sets the new selection — both land in the same React
        // batch and the null from popupclose wins, silently deselecting
        // the marker you just clicked. Simplest correct fix: only clicking
        // a marker changes the selection; there's no explicit "deselect"
        // action, which is fine for a debugging tool.
      }}
    >
      <Popup>
        <div className="min-w-[180px] text-sm">
          <div className="flex items-center gap-2 mb-1">
            <span className="font-medium">
              {position.carrier_code}
              {position.flight_number}
            </span>
            <StatusBadge value={position.status} colors={FLIGHT_STATUS_COLORS} />
            {inWallet && (
              <span className="text-xs px-2 py-0.5 rounded font-medium bg-violet-100 text-violet-800">
                in your wallet
              </span>
            )}
          </div>
          <AirportRoute origin={position.origin_airport} dest={position.dest_airport} />
          <div className="text-xs text-slate-600 mt-2 space-y-0.5">
            <div>Altitude: {metersToFeet(position.altitude_m) ?? '—'} ft</div>
            <div>Speed: {msToKnots(position.velocity_ms) ?? '—'} kts</div>
            <div>Heading: {position.heading != null ? `${Math.round(Number(position.heading))}°` : '—'}</div>
            <div>Last position: {new Date(position.polled_at).toLocaleTimeString()}</div>
          </div>
          <label className="flex items-center gap-1.5 mt-2 text-xs text-slate-600">
            <input type="checkbox" checked={showTrail} onChange={() => onToggleTrail()} />
            Show trail
          </label>
        </div>
      </Popup>
    </Marker>
  )
}

export default function MapPage() {
  const [selectedId, setSelectedId] = useState(null)
  const [showTrail, setShowTrail] = useState(false)

  const { data: positions, isLoading, error } = useQuery({
    queryKey: ['positions-current'],
    queryFn: getCurrentPositions,
    refetchInterval: POLL_INTERVAL_MS,
  })

  // Which currently-airborne flights you also have an active pick on, so
  // they can be highlighted on the map — separate query/poll from
  // positions since wallet composition changes far less often, but polled
  // at the same interval for simplicity.
  const { data: wallet } = useQuery({
    queryKey: ['wallet'],
    queryFn: getWallet,
    refetchInterval: POLL_INTERVAL_MS,
  })
  const walletFlightInstanceIds = new Set(
    (wallet?.picks ?? []).filter((p) => p.status === 'active').map((p) => p.flight_instance.id)
  )

  const handleSelect = (id) => {
    setSelectedId(id)
    setShowTrail(false) // trail is off by default whenever selection changes
  }

  if (isLoading) return <p>Loading live positions…</p>
  if (error) return <p className="text-red-600">Failed to load positions: {error.message}</p>

  const withCoords = positions.filter((p) => p.latitude != null && p.longitude != null)
  const inWalletCount = withCoords.filter((p) => walletFlightInstanceIds.has(p.flight_instance_id)).length

  return (
    <div>
      <div className="flex items-baseline gap-3 mb-1">
        <h1 className="text-xl font-semibold">Live Map</h1>
        <span className="text-sm text-slate-500">{withCoords.length} airborne</span>
        <span className="text-xs text-slate-400">auto-refreshing every {POLL_INTERVAL_MS / 1000}s</span>
      </div>
      <div className="flex items-center gap-3 mb-3 text-xs text-slate-500">
        <span className="inline-flex items-center gap-1">
          <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ backgroundColor: '#2563eb' }} />
          other flights
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ backgroundColor: '#7c3aed' }} />
          in your wallet ({inWalletCount})
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ backgroundColor: '#dc2626' }} />
          selected
        </span>
      </div>

      <div className="border border-slate-200 rounded overflow-hidden" style={{ height: '70vh' }}>
        <MapContainer center={US_CENTER} zoom={US_ZOOM} style={{ height: '100%', width: '100%' }}>
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          {withCoords.map((p) => (
            <PlaneMarker
              key={p.flight_instance_id}
              position={p}
              selected={p.flight_instance_id === selectedId}
              inWallet={walletFlightInstanceIds.has(p.flight_instance_id)}
              onSelect={handleSelect}
              showTrail={p.flight_instance_id === selectedId && showTrail}
              onToggleTrail={() => setShowTrail((v) => !v)}
            />
          ))}
          {selectedId && showTrail && <FlightTrail flightInstanceId={selectedId} />}
        </MapContainer>
      </div>
    </div>
  )
}
