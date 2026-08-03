from fastapi import FastAPI

app = FastAPI(title="ATC Survival Wallet")


@app.get("/health")
def health():
    return {"status": "ok"}
