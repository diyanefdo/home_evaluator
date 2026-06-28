# Setting up Google sign-in (OAuth)

This walks through enabling the **"Sign in with Google"** accounts feature end to
end. It's a one-time, free setup. Until you do this, the app runs fine without
login (the sign-in widget simply doesn't appear).

## What you're creating, and why

"Sign in with Google" needs Google to trust your app. You register the app once in
the Google Cloud Console and get two secrets:

- **Client ID** — public identifier for your app.
- **Client Secret** — proves requests really come from your app (keep private).

The flow at runtime: user clicks **Sign in** → the app sends Google your Client ID
→ Google logs the user in → Google redirects back to your app's **redirect URI**
with a one-time code → the app swaps that code (using the Client Secret) for the
user's email/name. Google rejects the whole thing unless the app is registered and
the redirect URI matches exactly.

## Step 1 — Create / pick a project

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (or select an existing one) from the project picker.

## Step 2 — Configure the OAuth consent screen

**APIs & Services → OAuth consent screen**

1. User type: **External**.
2. Fill in **App name**, **User support email**, and a **Developer contact email**.
3. **Scopes**: add `openid`, `email`, `profile` (these are non-sensitive — no
   Google verification review required).
4. **Test users**: while the app is in *Testing* mode, only Google accounts listed
   here can sign in. Add your own email (and anyone else who should log in).

> Testing vs Production: *Testing* limits sign-in to your listed test users.
> Because only basic scopes are used, you can later **Publish** to *Production*
> (anyone can sign in) without the app-verification review.

## Step 3 — Create the OAuth client ID

**APIs & Services → Credentials → Create credentials → OAuth client ID**

1. Application type: **Web application**.
2. Give it a name (e.g. "Home Evaluator").
3. **Authorized redirect URIs** — add the exact callback URL(s) the app will use:
   - Local testing: `http://localhost:8000/auth/google/callback`
   - Live site (e.g. Tailscale Funnel / public host):
     `https://<your-host>/auth/google/callback`
   You can add more than one. They must match **exactly** — scheme (`http`/`https`),
   host, and the `/auth/google/callback` path.
4. Click **Create**. Copy the **Client ID** and **Client Secret**.

## Step 4 — Put the credentials in `.env`

Copy `.env.example` to `.env` (it's gitignored) and fill in:

```bash
# Postgres password for the bundled db container
POSTGRES_PASSWORD=pick-a-strong-password

# From the OAuth client you just created
GOOGLE_CLIENT_ID=123456-abc.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxx

# Signs the session cookie — generate a long random value:
#   python -c "import secrets; print(secrets.token_hex(32))"
EVALUATOR_SECRET_KEY=replace-with-random-hex

# Only if the auto-detected callback URL is wrong (e.g. behind a proxy / Funnel).
# Must EXACTLY match a redirect URI you registered in step 3.
# EVALUATOR_OAUTH_REDIRECT=https://<your-host>/auth/google/callback
```

## Step 5 — Build and run

```bash
docker compose up -d --build
```

Verify:

```bash
curl localhost:8000/healthz     # expect {"status":"ok","accounts":true,"db":true}
```

Open the site — a **Sign in with Google** button appears top-right. After signing
in it shows your name + **Sign out**.

## Troubleshooting

- **`redirect_uri_mismatch`** — the callback URL the app sent isn't in the
  client's Authorized redirect URIs. Make them identical (watch `http` vs `https`,
  trailing slashes, and the host). Behind Tailscale Funnel, set
  `EVALUATOR_OAUTH_REDIRECT` to the public `https://…/auth/google/callback`.
- **"App isn't verified" / can't sign in** — you're not on the consent screen's
  **Test users** list (Testing mode), or publish to Production.
- **`"accounts": false` in `/healthz`** — one of `EVALUATOR_DB`,
  `GOOGLE_CLIENT_ID`, or `GOOGLE_CLIENT_SECRET` is missing, or the image wasn't
  rebuilt (`--build`).
- **Sessions reset on restart** — you didn't set `EVALUATOR_SECRET_KEY`, so a
  random key is generated each boot. Set a fixed value in `.env`.

## Security notes

- The **Client Secret** and `EVALUATOR_SECRET_KEY` live only in `.env` (gitignored)
  — never commit them.
- User records (email/name) are stored in Postgres = personal data; see the
  PIPEDA/privacy notes in [`ROADMAP.md`](ROADMAP.md) before going public.
