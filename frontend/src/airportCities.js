/**
 * IATA airport code -> display city name, for the 72 airports actually
 * used across the active flight_definitions pool (checked directly against
 * the DB, not assumed — see below).
 *
 * Source: OpenFlights airports.dat
 * (https://raw.githubusercontent.com/jpatokal/openflights/master/data/airports.dat),
 * downloaded fresh and parsed for this file — not written from memory.
 * 71 of our 72 codes were found directly; XWA (Williston, ND) is missing
 * because OpenFlights' static data still only has the airport's old code,
 * ISN ("Sloulin Field International Airport" / city "Williston") — XWA
 * replaced it in 2019 when the new Williston Basin International Airport
 * opened. Added manually below, same city, clearly flagged.
 *
 * Spot-checked against expectations before finalizing: LAX -> Los Angeles,
 * JFK -> New York, ORD -> Chicago, ATL -> Atlanta, DFW -> Dallas-Fort Worth,
 * SEA -> Seattle, BOS -> Boston, MIA -> Miami, PHX -> Phoenix, plus smaller
 * markets ASE -> Aspen, PRC -> Prescott, JLN -> Joplin — all correct.
 *
 * Three small-market entries had genuine data-quality issues in
 * OpenFlights' own City field (not a parsing error — verified against the
 * raw CSV row), corrected here:
 *   JAC: source has "Jacksn Hole" (typo) -> "Jackson Hole"
 *   RDU: source has "Raleigh-durham" (bad casing) -> "Raleigh-Durham"
 *   ROA: source has "Roanoke VA" (state suffix, inconsistent with every
 *        other entry) -> "Roanoke"
 *
 * DCA and IAD both legitimately map to "Washington" (Reagan National /
 * Dulles) — not a bug, just two airports serving the same city, exactly
 * the kind of duplicate the city-names-only approach expects.
 */
export const AIRPORT_CITIES = {
  ASE: 'Aspen',
  ATL: 'Atlanta',
  AUS: 'Austin',
  BIL: 'Billings',
  BIS: 'Bismarck',
  BNA: 'Nashville',
  BOS: 'Boston',
  BPT: 'Beaumont',
  CAE: 'Columbia',
  CAK: 'Akron',
  CLE: 'Cleveland',
  CLT: 'Charlotte',
  CMH: 'Columbus',
  CMX: 'Hancock',
  COS: 'Colorado Springs',
  CRW: 'Charleston',
  DCA: 'Washington',
  DEN: 'Denver',
  DFW: 'Dallas-Fort Worth',
  DTW: 'Detroit',
  EUG: 'Eugene',
  FAR: 'Fargo',
  FCA: 'Kalispell',
  FLG: 'Flagstaff',
  FLL: 'Fort Lauderdale',
  GEG: 'Spokane',
  GRR: 'Grand Rapids',
  HOB: 'Hobbs',
  IAD: 'Washington',
  IAH: 'Houston',
  JAC: 'Jackson Hole', // source: "Jacksn Hole" (typo), corrected
  JFK: 'New York',
  JLN: 'Joplin',
  JST: 'Johnstown',
  LAS: 'Las Vegas',
  LAX: 'Los Angeles',
  LGA: 'New York',
  LIT: 'Little Rock',
  MAF: 'Midland',
  MCO: 'Orlando',
  MIA: 'Miami',
  MKE: 'Milwaukee',
  MOT: 'Minot',
  MSP: 'Minneapolis',
  ORD: 'Chicago',
  ORF: 'Norfolk',
  PDX: 'Portland',
  PHL: 'Philadelphia',
  PHX: 'Phoenix',
  PNS: 'Pensacola',
  PRC: 'Prescott',
  RDD: 'Redding',
  RDU: 'Raleigh-Durham', // source: "Raleigh-durham" (casing), corrected
  RKS: 'Rock Springs',
  RNO: 'Reno',
  ROA: 'Roanoke', // source: "Roanoke VA" (state suffix), corrected
  SAN: 'San Diego',
  SAT: 'San Antonio',
  SAV: 'Savannah',
  SBN: 'South Bend',
  SBP: 'San Luis Obispo',
  SDF: 'Louisville',
  SEA: 'Seattle',
  SFO: 'San Francisco',
  SGU: 'Saint George',
  SJU: 'San Juan',
  SLC: 'Salt Lake City',
  SMF: 'Sacramento',
  SNA: 'Santa Ana',
  VCT: 'Victoria',
  XNA: 'Bentonville',
  XWA: 'Williston', // not in OpenFlights under this code — see file docstring
}

export function airportCity(code) {
  return AIRPORT_CITIES[code] ?? null
}
