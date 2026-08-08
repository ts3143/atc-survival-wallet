from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.flights import router as flights_router
from src.api.positions import router as positions_router
from src.api.wallet import router as wallet_router

app = FastAPI(title="ATC Survival Wallet")

# M4 frontend (Vite dev server) runs on a different origin locally.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(flights_router)
app.include_router(wallet_router)
app.include_router(positions_router)


@app.get("/health")
def health():
    return {"status": "ok"}
