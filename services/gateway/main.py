import hashlib  # Gives us the SHA-256 algorithm. We pass it into hmac.new() below.
import hmac  # Builds and compares the signature. Always compare with hmac.compare_digest(), never with ==.


import httpx  # HTTP client that works with async code. We use it to send the event to the next service.
from fastapi import FastAPI, HTTPException, Request  # The app object, the error we raise, and the raw request.
from prometheus_fastapi_instrumentator import Instrumentator  # Adds request metrics without writing any code.

from models import Settings  # Our settings class. It reads values from environment variables or a .env file.

settings = Settings()  # Read the settings once when the file loads, not on every request.
app = FastAPI()  # The application object. Uvicorn runs this.
Instrumentator().instrument(app).expose(app)  # Measure every route and publish the numbers at /metrics.


@app.get("/health")
async def health():  # WHAT THIS DOES: Tells Docker, Kubernetes, and the load balancer that this service is alive.
    return {"status": "ok"}  # Answers without touching the database, so it stays fast and never fails by accident.


@app.post("/webhook/github")
async def github_webhook(request: Request):  # WHAT THIS DOES: Checks the request truly came from GitHub, then passes it on.
    body = await request.body()  # Read the raw bytes. GitHub signed these exact bytes, so we must not change them.
    signature_header = request.headers.get("X-Hub-Signature-256", "")  # The signature GitHub sent. Empty if missing, so the check below fails.

    expected = (  # Build the signature ourselves, then compare it with the one GitHub sent.
        "sha256="  # GitHub puts the algorithm name in front of its signature, so we do the same.
        + hmac.new(
            settings.github_webhook_secret.encode(),  # The secret we and GitHub both know. Only we two can build this value.
            body,  # The exact bytes we received.
            hashlib.sha256,
        ).hexdigest()
    )

    if not hmac.compare_digest(expected, signature_header):  # Compares in constant time, so an attacker cannot guess it by timing.
        raise HTTPException(status_code=401, detail="Invalid signature")  # Not from GitHub, so stop here and answer 401.

    async with httpx.AsyncClient() as client:  # The with block closes the connection for us, even if something fails.
        response = await client.post(
            "http://webhook:8001/events",  # "webhook" is the Docker Compose service name. This address only works inside our network.
            content=body,  # Send the original bytes, unchanged.
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()  # If the next service fails, we fail too, and GitHub will send the event again later.

    return {"status": "ok"}  # GitHub only checks the status code. It ignores this body.

# ---------------------------------------------------------------------------
# WHAT THIS FILE IS FOR
# This is the gateway service. It is the only part of the system open to the
# public internet, so it is the front door and the guard at that door.
# GitHub calls it every time something happens on a pull request.
# Its one job is to prove the request really came from GitHub. It takes the
# raw body, builds a SHA-256 signature from it using the secret that we and
# GitHub share, and compares that with the X-Hub-Signature-256 header.
# If the two do not match, the request is a fake. We answer 401 and it goes
# no further. If they do match, we forward the untouched body to the internal
# webhook service, which does the real work. This file also answers /health so
# the platform knows we are alive, and /metrics so Prometheus can watch us.
# Keep this file small. No other logic should run before the signature check.
# ---------------------------------------------------------------------------
