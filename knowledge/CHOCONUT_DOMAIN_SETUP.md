# Put the Home Evaluator at choconut.ca/home_evaluator

The app and PostgreSQL database continue to run on this PC. Cloudflare Tunnel
provides HTTPS and forwards requests to Docker without opening router ports or
exposing the PC's public IP.

## 1. Put the domain on Cloudflare DNS

1. Create or sign in to a Cloudflare account and select **Add a domain**.
2. Enter `choconut.ca` and select the Free plan.
3. Cloudflare will assign two nameservers. Keep that page open.
4. In the WHC Client Area, open **Domains → choconut.ca → Nameservers** and
   replace the WHC parking nameservers with the two Cloudflare nameservers.
5. If WHC shows DNSSEC as enabled, disable it before changing nameservers.
6. Wait until Cloudflare marks the domain **Active**.

Changing nameservers delegates DNS only. WHC remains the registrar, and the
domain registration stays in the same account.

## 2. Create the tunnel

1. In Cloudflare, open **Networking → Tunnels → Create Tunnel**.
2. Choose **Cloudflared**, name it `home-evaluator`, and choose the Docker
   connector instructions.
3. Copy only the long token after `--token`. Anyone holding it can run the
   tunnel, so do not paste it into chat or commit it to Git.
4. Open this project's git-ignored `.env` file and set:

   ```text
   CLOUDFLARE_TUNNEL_TOKEN=the-token-from-cloudflare
   ```

5. In the tunnel's **Routes**, add a **Published application**:

   - Hostname: `choconut.ca` (leave the subdomain blank)
   - Service type: `HTTP`
   - Service URL: `http://home-evaluator:8000`

The app itself handles the `/home_evaluator` prefix, so Cloudflare must forward
the original path without rewriting it.

## 3. Start and verify

From this directory:

```bash
docker compose --profile cloudflare up -d
docker compose --profile cloudflare ps
docker compose logs --tail=50 cloudflared
```

Then open:

```text
https://choconut.ca/home_evaluator/
```

Both `home-evaluator` and `home-evaluator-tunnel` use `restart: unless-stopped`.
Docker Desktop must also be configured to start when Windows signs in for the
site to return automatically after a reboot.

## 4. Switch Google sign-in after the domain works

In the Google Cloud OAuth client's **Authorized redirect URIs**, add:

```text
https://choconut.ca/home_evaluator/auth/google/callback
```

Then change `EVALUATOR_OAUTH_REDIRECT` in `.env` to that exact URL and recreate
the app:

```bash
docker compose --profile cloudflare up -d --force-recreate home-evaluator cloudflared
```

Keep the existing Tailscale callback registered until sign-in through the new
domain has been tested.
