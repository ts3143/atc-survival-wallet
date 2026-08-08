import { airportCity } from '../airportCities.js'

/** IATA code with a small city-name subheader underneath, e.g.
 *    LAX
 *    Los Angeles
 */
export function AirportCode({ code, className = '' }) {
  const city = airportCity(code)
  return (
    <span className={`inline-flex flex-col leading-tight ${className}`}>
      <span>{code}</span>
      {city ? (
        <span className="text-[10px] text-slate-400 font-normal">{city}</span>
      ) : (
        <span className="text-[10px] text-slate-300 font-normal">—</span>
      )}
    </span>
  )
}

/** origin -> dest pair, each with its city subheader. */
export function AirportRoute({ origin, dest, className = '' }) {
  return (
    <span className={`inline-flex items-start gap-1 ${className}`}>
      <AirportCode code={origin} />
      <span className="text-slate-400 mx-0.5 mt-[1px]">→</span>
      <AirportCode code={dest} />
    </span>
  )
}
