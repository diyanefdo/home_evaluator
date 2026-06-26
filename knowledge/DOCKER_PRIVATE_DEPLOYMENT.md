# Running the Home Evaluator in Docker, reachable over a private link

Goal: run this tool as a small web service inside a Docker container on your PC,
then reach it from your phone/laptop anywhere through a **private** link (only
your own devices — not the public internet).

The tool today is a CLI that writes PNG charts. To "ping it through a link" it
needs a thin web layer. The plan:

1. Add a small web app (FastAPI) that wraps the existing `evaluator` package.
2. Containerize it with Docker.
3. Expose it privately with a tunnel (Tailscale recommended).

> You are on **WSL2 + Windows**, so there are a couple of WSL-specific notes
> called out below.

---

## 1. Add a thin web layer

Create `webapp.py` in the project root. It reuses the existing pipeline
(`cli.build_engine_params` → `projections` → `charts`) and returns an HTML page
with the five charts embedded inline (no file serving needed).

```python
# webapp.py
import argparse
import base64
import tempfile
import shutil

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from evaluator import cli, projections, charts

app = FastAPI(title="Canadian Buy-vs-Rent Home Evaluator")

FORM = """
<!doctype html><html><head><title>Home Evaluator</title>
<style>body{font-family:sans-serif;max-width:760px;margin:2rem auto}
label{display:block;margin:.6rem 0 .2rem}input{width:100%;padding:.4rem}
button{margin-top:1rem;padding:.6rem 1.2rem}</style></head><body>
<h1>Canadian Buy-vs-Rent Evaluator</h1>
<form action="/evaluate">
  <label>House price</label><input name="price" value="1000000">
  <label>Down payment ($ or %)</label><input name="down" value="200000">
  <label>Mortgage term (years)</label><input name="years" value="30">
  <label>Postal code</label><input name="postal" value="M2J 0E8">
  <button type="submit">Evaluate</button>
</form></body></html>
"""


@app.get("/", response_class=HTMLResponse)
def home():
    return FORM


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/evaluate", response_class=HTMLResponse)
def evaluate(price: float, down: str = "20%", years: int = 30, postal: str = "M2J 0E8"):
    # Mirror the CLI's argument shape so we reuse its validated param mapping.
    args = argparse.Namespace(
        price=price, down=down, years=years, postal=postal,
        rate=None, appreciation=None, rent=None, rent_growth=None,
        property_tax_rate=None, investment_return=None,
        insurance=1500.0, hoa=0.0, out=None, no_charts=False,
    )
    params = cli.build_engine_params(args)
    projection = projections.build_projection(params)
    summary = projections.compute_summary(projection, params)

    out_dir = tempfile.mkdtemp(prefix="charts_")
    try:
        paths = charts.generate_charts(projection, params, out_dir)
        imgs = ""
        for p in paths:
            with open(p, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode()
            imgs += f'<img src="data:image/png;base64,{b64}" style="width:100%;margin:1rem 0">'
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)

    cy = summary.get("crossover_year")
    cross = f"~year {cy:.1f}" if cy else "never (within term)"
    final_gap = summary["final_buyer_minus_renter"]
    leader = "buyer" if final_gap >= 0 else "renter"
    return f"""
    <html><body style="font-family:sans-serif;max-width:1000px;margin:2rem auto">
    <h1>{params['region_label']}</h1>
    <p><b>{years}yr @ {params['mortgage_rate']*100:.2f}%</b> &middot;
       crossover {cross} &middot;
       year-{years} gap <b>${abs(final_gap):,.0f}</b> in favour of the {leader}</p>
    {imgs}
    <p><a href="/">&larr; new analysis</a></p>
    </body></html>
    """
```

Add the web dependencies to a `requirements.txt` in the project root:

```text
numpy
matplotlib
fastapi
uvicorn[standard]
```

Test it locally before containerizing:

```bash
pip install -r requirements.txt
uvicorn webapp:app --host 0.0.0.0 --port 8000
# open http://localhost:8000
```

---

## 2. Containerize it

Create `Dockerfile` in the project root:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install deps first so this layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code.
COPY evaluator/ ./evaluator/
COPY webapp.py .

# matplotlib uses the Agg backend (already set in charts.py) — no display needed.
# Give it a writable, container-local config dir.
ENV MPLCONFIGDIR=/tmp/mpl

EXPOSE 8000
CMD ["uvicorn", "webapp:app", "--host", "0.0.0.0", "--port", "8000"]
```

Create `.dockerignore` so build context stays small:

```text
.git
charts_output
_chart_preview
__pycache__
*.pyc
.claude
knowledge
notes.txt
```

Optional `docker-compose.yml` (nice for `up -d` + auto-restart):

```yaml
services:
  home-evaluator:
    build: .
    image: home-evaluator
    ports:
      - "8000:8000"          # host:container — see WSL note below
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"]
      interval: 30s
      timeout: 5s
      retries: 3
```

Build and run:

```bash
docker compose up -d --build      # or: docker build -t home-evaluator . && docker run -d -p 8000:8000 home-evaluator
docker compose logs -f            # watch startup
curl http://localhost:8000/healthz   # -> {"status":"ok"}
```

> **WSL2 note.** If you use **Docker Desktop** (WSL2 backend), published ports
> are auto-forwarded to Windows `localhost`, so `http://localhost:8000` works
> from the Windows browser directly. If you run the Docker engine *inside* a WSL
> distro without Docker Desktop, the port is on the WSL VM's IP; reach it with
> the distro IP (`hostname -I`) or enable
> [`localhostForwarding`](https://learn.microsoft.com/windows/wsl/wsl-config)
> in `.wslconfig` (on by default).

---

## 3. Expose it through a private link

Pick one. **Tailscale is the best fit** for "only my devices can reach it."

### Option A — Tailscale (recommended: private mesh VPN, stable name)

Tailscale puts your PC and your other devices on a private network. Nothing is
exposed to the public internet; only devices logged into *your* tailnet can
connect.

1. Install Tailscale on the **Windows host** and on the device you'll connect
   from (phone/laptop): https://tailscale.com/download — sign in on both with
   the same account.
2. With the container running and port `8000` published to the host, your other
   tailnet devices can reach it at:
   ```
   http://<your-pc-name>:8000          # MagicDNS name, e.g. http://my-desktop:8000
   http://100.x.y.z:8000               # or the PC's Tailscale IP
   ```
3. (Nicer) Put HTTPS in front, still private to your tailnet, with **Tailscale
   Serve**:
   ```bash
   tailscale serve --bg 8000
   tailscale serve status              # shows https://<my-desktop>.<tailnet>.ts.net
   ```
   That HTTPS URL is your private link — reachable by your devices, no one else.

> Do **not** use `tailscale funnel` unless you intend to publish it to the whole
> internet — Funnel is the public counterpart of Serve.
>
> **WSL2 note.** Simplest is Tailscale on **Windows** (it shares the host, and
> the published Docker port is on the host). You *can* run Tailscale inside WSL
> or in a sidecar container, but the Windows-host install is the least fuss.

### Option B — Cloudflare Tunnel + Access (private link with login)

Gives a `https://evaluator.yourdomain.com` URL gated behind Cloudflare Access
(e.g. "only my Google email can open it"). Needs a domain on Cloudflare.

```bash
# install cloudflared, then:
cloudflared tunnel login
cloudflared tunnel create home-evaluator
cloudflared tunnel route dns home-evaluator evaluator.yourdomain.com
cloudflared tunnel --url http://localhost:8000 run home-evaluator
```
Then in the Cloudflare Zero Trust dashboard add an **Access** application over
`evaluator.yourdomain.com` with an email/identity policy. Without an Access
policy the hostname is public, so add the policy.

### Option C — ngrok (fastest, good for a quick share)

```bash
ngrok http 8000 --basic-auth "me:somepassword"
```
Prints a public `https://....ngrok-free.app` URL; `--basic-auth` keeps randoms
out. Fine for ad-hoc use; less "private" than A/B.

| Option | Private by default | Stable URL | Extra needs | Best for |
|--------|--------------------|-----------|-------------|----------|
| Tailscale | ✅ tailnet-only | ✅ MagicDNS | install on both devices | personal always-on access |
| Cloudflare Tunnel | ✅ with Access policy | ✅ your domain | a domain on Cloudflare | sharing with specific people |
| ngrok | ⚠️ only as strong as basic-auth | ❌ rotates (free) | account | quick one-off demo |

---

## 4. Security checklist

- The app holds **no secrets** (only public market assumptions), but the
  endpoint still runs compute on every request — don't leave it open.
- Keep `--host 0.0.0.0` **inside the container only**; rely on the tunnel for
  exposure. Do **not** port-forward 8000 on your home router.
- Prefer a layer that authenticates: Tailscale (device identity), Cloudflare
  Access (login), or at least ngrok `--basic-auth`.
- If you ever expose it more widely, add rate-limiting and input bounds (cap
  `years`, validate `price`/`down`) to the FastAPI handler.

---

## 5. Quick reference

```bash
# build + run
docker compose up -d --build
# verify
curl http://localhost:8000/healthz
# private HTTPS link (Tailscale)
tailscale serve --bg 8000 && tailscale serve status
# logs / stop
docker compose logs -f
docker compose down
```

Files this guide adds to the repo (not yet created — ask if you want them
scaffolded): `webapp.py`, `requirements.txt`, `Dockerfile`, `.dockerignore`,
`docker-compose.yml`.
