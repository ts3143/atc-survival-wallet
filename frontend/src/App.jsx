import { NavLink, Route, Routes } from 'react-router-dom'
import FlightsPage from './pages/FlightsPage.jsx'
import DraftPage from './pages/DraftPage.jsx'
import WalletPage from './pages/WalletPage.jsx'
import MapPage from './pages/MapPage.jsx'

const navLinkClass = ({ isActive }) =>
  `px-3 py-2 rounded ${isActive ? 'bg-slate-900 text-white' : 'text-slate-700 hover:bg-slate-100'}`

function App() {
  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="max-w-5xl mx-auto px-4 py-3 flex items-center gap-4">
          <span className="font-semibold text-slate-900">ATC Survival Wallet</span>
          <nav className="flex gap-1">
            <NavLink to="/" end className={navLinkClass}>
              Flights
            </NavLink>
            <NavLink to="/wallet" className={navLinkClass}>
              Wallet
            </NavLink>
            <NavLink to="/map" className={navLinkClass}>
              Map
            </NavLink>
          </nav>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-4 py-6">
        <Routes>
          <Route path="/" element={<FlightsPage />} />
          <Route path="/draft/:flightId" element={<DraftPage />} />
          <Route path="/wallet" element={<WalletPage />} />
          <Route path="/map" element={<MapPage />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
