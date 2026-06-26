# Restarting the Home Evaluator after a PC reboot

What to do to get the app + public link back up after restarting your Windows PC.

> **Good news: most of this is automatic.** The container is set to
> `restart: unless-stopped`, and both Docker Desktop and Tailscale (Windows)
> start on login. So after a reboot the app usually comes back on its own — the
> steps below are mostly *verification*, plus what to run when you've changed the
> code.

---

## TL;DR

```bash
# In the WSL (Ubuntu) terminal, in the project folder:
cd ~/home_evaluator
docker compose up -d --build        # rebuilds + (re)starts; safe to run anytime
curl http://localhost:8000/healthz  # expect {"status":"ok"}
```

```powershell
# In Windows PowerShell:
tailscale status                    # confirm you're logged in / connected
tailscale funnel status             # confirm the public https URL is live
```

If both health checks pass, you're done — the public URL is the same as before.

---

## 1. Make sure Docker Desktop is running (Windows)

- Open **Docker Desktop** if it isn't already (look for the whale icon in the
  system tray). The container only runs while Docker Desktop is up.
- *Set it to auto-start (recommended):* Docker Desktop → **Settings → General →**
  ✅ *Start Docker Desktop when you log in*. Then it's running before you even
  open a terminal.

## 2. Start / verify the container (WSL terminal)

Open your **Ubuntu (WSL)** terminal and go to the project folder:

```bash
cd ~/home_evaluator
```

- **Just verify it's up:**
  ```bash
  docker compose ps               # STATUS should say "running"/"healthy"
  curl http://localhost:8000/healthz   # {"status":"ok"}
  ```
- **If it's not running, or you changed the password (.env):**
  ```bash
  docker compose up -d            # (re)start, picks up .env changes
  ```
- **If you pulled new code and want the latest version live:**
  ```bash
  git pull
  docker compose up -d --build    # --build rebuilds the image with new code
  ```

> `--build` is only needed when the **code** changed. For a plain restart,
> `docker compose up -d` is enough; `docker compose restart` just bounces it.

## 3. Start / verify Tailscale (Windows)

Tailscale installs as a Windows service and normally reconnects on boot.

- Confirm it's connected: tray icon → should show your account/tailnet, or in
  PowerShell:
  ```powershell
  tailscale status
  ```
- Confirm the public link is still served:
  ```powershell
  tailscale funnel status
  ```
  This prints your permanent URL: `https://<your-pc>.<your-tailnet>.ts.net`.
- *If Funnel isn't shown* (rare — e.g. it was reset), re-enable it:
  ```powershell
  tailscale funnel --bg 8000
  ```
- *Keep Tailscale always-on (recommended):* tray icon → **Preferences →**
  ✅ *Run unattended* (stays connected even when you're not logged in).

---

## Quick reference

| I want to… | Where | Command |
|------------|-------|---------|
| Check app is up | WSL | `curl http://localhost:8000/healthz` |
| Start / restart app | WSL | `docker compose up -d` |
| Apply new code | WSL | `git pull && docker compose up -d --build` |
| Bounce without rebuild | WSL | `docker compose restart` |
| Stop the app | WSL | `docker compose down` |
| View logs | WSL | `docker compose logs -f` |
| Check Tailscale | Windows | `tailscale status` |
| Check public URL | Windows | `tailscale funnel status` |
| Re-publish public URL | Windows | `tailscale funnel --bg 8000` |

---

## Troubleshooting

- **`docker compose` says "Cannot connect to the Docker daemon"** → Docker
  Desktop isn't running. Start it (step 1) and retry.
- **Public URL loads for you but not others** → check `tailscale funnel status`
  shows the URL; make sure you used **Funnel** (public), not **Serve** (tailnet
  only).
- **Browser shows old styling/behaviour after an update** → hard-refresh
  (`Ctrl+Shift+R`); confirm you ran `docker compose up -d --build` after `git pull`.
- **Login prompt rejects the new password** → you changed `.env` but didn't
  restart: run `docker compose up -d`. Also close cached browser tabs.
- **`http://localhost:8000` works on Windows but the funnel doesn't** → run
  Tailscale on the **Windows host** (it can reach the published port), not inside
  WSL.
