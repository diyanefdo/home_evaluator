# Self-host a Docker web project with Cloudflare Tunnel

This guide publishes a web application from your own PC at an HTTPS domain
without opening router ports or exposing the PC's public IP. It uses Docker
Compose and a remotely managed Cloudflare Tunnel.

```text
Visitor
  -> Cloudflare HTTPS and DNS
  -> outbound Cloudflare Tunnel
  -> cloudflared container
  -> application container
```

For a new project, a dedicated subdomain such as `tool.example.com` is the
simplest option. Hosting below a path such as `example.com/tool` also works, but
the application or a reverse proxy must understand that path prefix.

## 1. Choose the deployment values

Write down these values before starting:

| Setting | Example |
| --- | --- |
| Registered domain | `example.com` |
| Public hostname | `tool.example.com` |
| Optional URL prefix | none, or `/tool` |
| Docker application service | `app` |
| Application container port | `8000` |
| Tunnel name | `tool-production` |
| Local health endpoint | `/healthz` |
| OAuth callback, if used | `https://tool.example.com/auth/google/callback` |

The resulting public URL is either:

```text
https://tool.example.com/
```

or, for a path-based deployment:

```text
https://example.com/tool/
```

DNS selects a hostname, not a URL path. Cloudflare Tunnel forwards the incoming
request path to the origin; it does not automatically remove `/tool`.

## 2. Prepare the application

Before involving Cloudflare, confirm that the application:

- runs successfully in Docker;
- listens on `0.0.0.0`, not only `127.0.0.1`, inside its container;
- has a health endpoint, preferably `/healthz`;
- generates HTTPS-aware links and redirects when behind a proxy; and
- supports the chosen URL prefix if using a path-based URL.

For a subdomain, the app normally serves `/` and needs no prefix setting. For a
path deployment, use the framework's base-path or mount-path feature, or place a
reverse proxy such as Caddy, nginx, or Traefik between the tunnel and the app.
Test forms, redirects, static files, API calls, logout, and OAuth callbacks under
the prefix—not only the home page.

## 3. Add `cloudflared` to Docker Compose

Adapt this example to the project's `compose.yaml` or `docker-compose.yml`:

```yaml
services:
  app:
    build: .
    expose:
      - "8000"
    restart: unless-stopped

  cloudflared:
    image: cloudflare/cloudflared:latest
    command: tunnel --no-autoupdate run
    environment:
      TUNNEL_TOKEN: "${CLOUDFLARE_TUNNEL_TOKEN}"
    depends_on:
      - app
    restart: unless-stopped
    profiles:
      - cloudflare
```

Important details:

- `app` is the Docker service name used later in the tunnel's Service URL.
- Both services are on the same default Compose network, so `cloudflared` can
  reach `http://app:8000`.
- `expose` makes the intended container port clear without publishing it on the
  PC. Keep an existing `ports` mapping only if local or LAN access is needed.
- If the app already has a Compose health check, `depends_on` can use
  `condition: service_healthy` so the connector waits for it.
- Pin the `cloudflared` image to a tested version instead of `latest` if the
  project requires reproducible deployments.

Create a git-ignored `.env` file beside the Compose file:

```dotenv
CLOUDFLARE_TUNNEL_TOKEN=
```

Ensure `.gitignore` contains:

```gitignore
.env
```

Never put the live tunnel token, OAuth client secret, database password, or
session secret in `.env.example` or Git.

## 4. Put the domain on Cloudflare DNS

Skip this section if the domain already shows **Active** in Cloudflare.

1. Add the domain to Cloudflare and select a plan.
2. Cloudflare assigns authoritative nameservers.
3. At the domain registrar, replace the old nameservers with the exact
   Cloudflare nameservers.
4. If DNSSEC was enabled at the old DNS provider, remove the old DS record or
   disable DNSSEC before changing nameservers. A stale DS record can prevent the
   new delegation from resolving.
5. In Cloudflare, wait for the zone status to become **Active**. A pending zone
   cannot proxy production traffic through Cloudflare.
6. After activation, enable DNSSEC through Cloudflare and follow the registrar
   instructions Cloudflare provides.

Changing nameservers does not transfer the registration. The original registrar
continues to bill and manage the domain registration.

## 5. Create a remotely managed tunnel

1. In Cloudflare, open **Networking -> Tunnels**.
2. Select **Create tunnel**, choose **Cloudflared**, and name the tunnel.
3. Choose the Docker connector instructions.
4. Copy only the long token after `--token` into the local `.env` file:

   ```dotenv
   CLOUDFLARE_TUNNEL_TOKEN=eyJ...
   ```

The token starts with `eyJ` and is much longer than the tunnel UUID. The UUID is
not a connector token. Anyone with the real token can run this tunnel, so rotate
it immediately if it is exposed.

## 6. Add the public application route

In the tunnel's **Routes**, add a **Published application**:

- **Hostname:** `tool.example.com`
- **Service type:** `HTTP`
- **Service URL:** `http://app:8000`

Use the Compose service name and the container port—not `localhost` and not
necessarily the PC's published port.

For an apex-domain deployment, use `example.com` as the hostname. For a
path-based deployment, either:

- leave the Cloudflare path blank and let the app handle `/tool`; or
- configure a path-specific route when several services share one hostname.

In both cases, confirm what path reaches the origin. The app must handle the
prefix or an intermediate reverse proxy must deliberately rewrite it. Order
path-specific routes before a catch-all route.

Cloudflare normally creates a proxied CNAME automatically when the route is
added. Confirm it under **DNS -> Records**:

```text
Type:    CNAME
Name:    tool (or @ for the apex)
Target:  <TUNNEL-UUID>.cfargotunnel.com
Proxy:   Proxied
TTL:     Auto
```

If an imported registrar-parking `A` or `AAAA` record already exists for the
same hostname, delete that conflicting record and create the tunnel CNAME. Do
not delete unrelated `MX`, `TXT`, or email records.

## 7. Start the deployment

From the project directory:

```bash
docker compose --profile cloudflare up -d --build
docker compose --profile cloudflare ps
docker compose --profile cloudflare logs --tail=100 cloudflared
```

Healthy connector logs normally contain:

- `Starting tunnel`;
- several `Registered tunnel connection` messages; and
- an ingress configuration with the expected hostname and Service URL.

If outbound UDP/QUIC is blocked, `cloudflared` may fall back to HTTP/2. That is
usually functional, although the logs can call it degraded transport.

After changing the token, recreate the connector:

```bash
docker compose --profile cloudflare up -d --force-recreate cloudflared
```

## 8. Verify the public application

Check more than the HTTP status. A registrar parking page can also return
`200 OK`.

```bash
curl -sS -D - -o /dev/null https://tool.example.com/
curl -fsS https://tool.example.com/healthz
curl -fsS https://tool.example.com/ | rg "Expected app title or text"
```

Also verify at least one static asset and one functional endpoint:

```bash
curl -sS -D - -o /dev/null https://tool.example.com/static/app.css
curl -sS -D - -o /dev/null "https://tool.example.com/api/example"
```

For a path deployment, include the prefix in every public check:

```bash
curl -sS -D - -o /dev/null https://example.com/tool/
curl -fsS https://example.com/tool/healthz
```

Finally, open the URL in a private browser window and exercise forms, redirects,
authentication, and logout.

## 9. Configure Google OAuth, if used

Create or edit an OAuth **Web application** client in Google Cloud. Add the exact
public callback to **Authorized redirect URIs**:

```text
https://tool.example.com/auth/google/callback
```

For a path deployment:

```text
https://example.com/tool/auth/google/callback
```

Configure the same exact URL in the app, then recreate the app container. Scheme,
hostname, path, case, and trailing slash must match. Add the new callback before
removing an old working callback.

Test the sign-in link. Success reaches Google's account selection or consent
screen. An `Error 400: redirect_uri_mismatch` means the callback sent by the app
does not exactly match an authorized URI for that OAuth client.

## 10. Make the PC reliable enough for hosting

`restart: unless-stopped` restarts containers, but it cannot start a powered-off
or sleeping PC. For unattended hosting:

- start Docker Engine or Docker Desktop automatically at login or boot;
- disable sleep and hibernation while hosting;
- use wired networking when practical;
- install OS, Docker, app, and dependency security updates;
- monitor free disk space and container health;
- back up database volumes and uploaded files off the PC; and
- consider a UPS for short power interruptions.

A changing residential public IP is normally harmless because the connector
opens an outbound tunnel. No router port forwarding is required.

## 11. Security checklist

- Keep `.env` out of Git and restrict who can read it.
- Rotate a tunnel token after accidental disclosure.
- Do not publish the app's host port unless it is needed locally.
- Require application authentication or Cloudflare Access if the app is not
  intended for everyone on the Internet.
- Use a strong, persistent session-signing secret.
- Use unique production database credentials and regular backups.
- Treat forwarded headers only from a trusted proxy.
- Do not expose development servers, debug modes, admin consoles, or database
  ports publicly.

## 12. Hosting additional projects on the same PC

The least error-prone layout is one subdomain per project:

```text
calculator.example.com -> calculator project
inventory.example.com  -> inventory project
notes.example.com      -> notes project
```

For separate Compose projects, using one remotely managed tunnel and one
`cloudflared` container per project keeps Docker networking simple. Each public
hostname gets its own tunnel CNAME and its own secret token.

A single connector can serve several projects, but it must share a Docker network
with every target service. Create an explicit external network, attach the apps
and connector to it, and add one published route per hostname. Avoid reusing a
service name that resolves to the wrong container.

Use multiple paths on one hostname only when there is a real reason. Every app
must handle its prefix, route priority becomes important, and cookies or OAuth
callbacks can collide more easily. Subdomains avoid most of those problems.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Cloudflare remains **Pending Nameserver Update** | Wrong nameservers or stale DNSSEC/DS record | Compare the registrar nameservers exactly; remove the old DS record; request another activation check |
| `cloudflared` says the token is invalid | A tunnel UUID or another short value was copied | Copy the long `eyJ...` connector token from **Add a replica** |
| Tunnel is healthy but the public site shows a registrar parking page | Old `A`/`AAAA` record still owns the hostname | Replace only that conflicting record with a proxied CNAME to `<UUID>.cfargotunnel.com` |
| Cloudflare returns `502 Bad Gateway` | Wrong Docker service name/port, app down, or app bound only to localhost | Test from the Compose network and make the app listen on `0.0.0.0` |
| Cloudflare returns error `1016` | DNS points to a tunnel with no live connector | Start/recreate `cloudflared` and check its token and logs |
| Home page works but assets or forms fail | App does not understand the URL prefix | Fix generated URLs and route handling, or use a reverse proxy to rewrite the prefix |
| Redirect loop or redirects to HTTP | Proxy headers or public base URL are wrong | Trust Cloudflare only as the proxy and configure the app's external HTTPS URL |
| Google reports `redirect_uri_mismatch` | URI differs by scheme, host, path, case, or slash | Copy the callback emitted by the app into Google's authorized redirect list exactly |
| Site disappears after reboot | Docker did not start, PC slept, or containers were manually stopped | Enable Docker startup, disable sleep, and inspect `docker compose ps` |

## Rollback

To take the public site offline while leaving the local app intact:

```bash
docker compose --profile cloudflare stop cloudflared
```

Then remove or replace the tunnel's DNS record and published route. DNS records
remain after a connector stops, so visitors may see a Cloudflare tunnel error
until the record is changed.

## Official references

- [Cloudflare Tunnel routing](https://developers.cloudflare.com/tunnel/concepts/routing/)
- [Cloudflare published applications](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/routing-to-tunnel/)
- [Cloudflare tunnel tokens](https://developers.cloudflare.com/tunnel/reference/tunnel-tokens/)
- [Cloudflare zone status](https://developers.cloudflare.com/dns/zone-setups/reference/domain-status/)
- [Cloudflare pending-nameserver troubleshooting](https://developers.cloudflare.com/dns/zone-setups/troubleshooting/pending-nameservers/)
- [Google OAuth for web-server applications](https://developers.google.com/identity/protocols/oauth2/web-server)
