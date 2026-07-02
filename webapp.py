"""FastAPI web layer for the Canadian buy-vs-rent home evaluator.

Wraps the existing ``evaluator`` package: takes the inputs from a form (or query
string), runs the projection pipeline, and returns a single HTML page that draws
all six charts interactively in the browser with a vendored Plotly (hover, legend
toggle, zoom) plus live what-if sliders. (The CLI still renders PNGs via
``evaluator.charts``; the web no longer rasterizes charts server-side.)

Run locally:
    uvicorn webapp:app --host 0.0.0.0 --port 8000
    # then open http://localhost:8000

See knowledge/DOCKER_PRIVATE_DEPLOYMENT.md for containerized + private-link use.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import secrets

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from evaluator import cli, data, geocode, projections, tax

# Optional Phase 2 stack (persistence + Google OAuth). Imported defensively so the
# app still runs as a stateless tool when the extra deps aren't installed.
try:
    from starlette.middleware.sessions import SessionMiddleware
    from authlib.integrations.starlette_client import OAuth, OAuthError
    from evaluator import db as eval_db, accounts, scenarios, history, shares, analytics
    _ACCOUNTS_IMPORTABLE = True
except Exception:  # noqa: BLE001 - any import problem just disables accounts
    _ACCOUNTS_IMPORTABLE = False

from pydantic import BaseModel

app = FastAPI(title="Canadian Buy-vs-Rent Home Evaluator")

# Vendored Plotly (no external CDN) for the interactive charts. Served open so the
# library loads regardless of basic-auth on the pages.
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

# --------------------------------------------------------------------------- #
# Optional HTTP basic-auth (recommended when exposed on a public link).
#
# Toggle with EVALUATOR_AUTH:
#   - unset       -> auto: auth is ON when EVALUATOR_PASSWORD is set, else OFF
#   - on/1/true   -> force ON  (requires EVALUATOR_PASSWORD, else startup error)
#   - off/0/false -> force OFF (no login, even if a password is configured)
# Username defaults to "admin"; override with EVALUATOR_USER.
# --------------------------------------------------------------------------- #
_AUTH_USER = os.environ.get("EVALUATOR_USER", "admin")
_AUTH_PASS = os.environ.get("EVALUATOR_PASSWORD")  # None/"" => no credentials set
_security = HTTPBasic(auto_error=False)

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}

# Live market data (Bank of Canada 5yr mortgage rate). ON by default for the web
# service; falls back to baked-in regional rates if offline. Disable with
# EVALUATOR_LIVE_DATA=off.
_LIVE_DATA = os.environ.get("EVALUATOR_LIVE_DATA", "on").strip().lower() not in _FALSY

# --------------------------------------------------------------------------- #
# Accounts (Phase 2): PostgreSQL persistence + Google OAuth sign-in.
#
# Fully opt-in and additive — the tool stays usable signed-out. Accounts light up
# only when the deps are installed, EVALUATOR_DB is set, and the Google client
# credentials are provided:
#   EVALUATOR_DB            postgresql+psycopg://eval:pass@db:5432/evaluator
#   GOOGLE_CLIENT_ID        from a Google Cloud OAuth 2.0 client
#   GOOGLE_CLIENT_SECRET
#   EVALUATOR_SECRET_KEY    signs the session cookie (set one in prod!)
#   EVALUATOR_OAUTH_REDIRECT  optional explicit callback URL (behind a proxy)
# --------------------------------------------------------------------------- #
_DB_ON = _ACCOUNTS_IMPORTABLE and eval_db.db_enabled()
_GOOGLE_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
_GOOGLE_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
_OAUTH_ON = bool(_DB_ON and _GOOGLE_ID and _GOOGLE_SECRET)
_SECRET_KEY = os.environ.get("EVALUATOR_SECRET_KEY", "").strip() or secrets.token_hex(32)
# Comma-separated emails allowed to view /admin (the analytics dashboard).
_ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("EVALUATOR_ADMIN_EMAILS", "").split(",") if e.strip()}

_oauth = None
if _OAUTH_ON:
    _oauth = OAuth()
    _oauth.register(
        name="google",
        client_id=_GOOGLE_ID,
        client_secret=_GOOGLE_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

# Signed-cookie sessions back the login state (needed for the OAuth handshake).
if _ACCOUNTS_IMPORTABLE:
    app.add_middleware(SessionMiddleware, secret_key=_SECRET_KEY,
                       same_site="lax", https_only=False)


@app.on_event("startup")
def _startup_init_db() -> None:
    """Create tables on boot when persistence is enabled (best-effort)."""
    if _DB_ON:
        try:
            eval_db.init_db()
        except Exception as exc:  # noqa: BLE001 - log and continue; tool still works
            print(f"[warn] database init failed ({exc}); accounts disabled this run.")


def _current_user(request: Request) -> dict | None:
    """The signed-in user (dict) for this request, or None."""
    if not _DB_ON:
        return None
    uid = request.session.get("user_id")
    if not uid:
        return None
    try:
        return accounts.get_user(uid)
    except Exception:  # noqa: BLE001 - DB hiccup shouldn't break page rendering
        return None


def _is_admin(user: dict | None) -> bool:
    return bool(user and (user.get("email") or "").lower() in _ADMIN_EMAILS)


def _auth_bar(user: dict | None) -> str:
    """Top-right sign-in / signed-in widget. Empty when accounts are disabled."""
    if not _OAUTH_ON:
        return ""
    if user:
        who = html.escape(user.get("name") or user.get("email") or "Account")
        admin_link = ('<a class="authbtn ghost" href="/admin">Admin</a>'
                      if _is_admin(user) else "")
        return (f'<div class="authbar"><span class="who">{who}</span>'
                f'<a class="authbtn ghost" href="/history">My runs</a>'
                f'<a class="authbtn ghost" href="/scenarios">My scenarios</a>'
                f'{admin_link}'
                f'<a class="authbtn" href="/logout">Sign out</a></div>')
    return ('<div class="authbar">'
            '<a class="authbtn" href="/login">Sign in with Google</a></div>')


def _page(page_html: str, user: dict | None) -> str:
    """Inject the auth widget just inside <body> for a rendered page."""
    return page_html.replace("<body>", "<body>" + _auth_bar(user), 1)


# Inputs we accept for a saved scenario (everything /evaluate understands).
_STR_INPUTS = ("down", "postal", "strategy")
_INT_INPUTS = ("years", "age")
_FLOAT_INPUTS = ("price", "income", "rate", "appreciation", "rent", "rent_growth",
                 "property_tax_rate", "investment_return", "insurance", "hoa",
                 "retirement_rate")


def _sanitize_inputs(raw: dict) -> dict:
    """Whitelist + coerce the inputs that reproduce an evaluation."""
    out: dict = {}
    for k in _STR_INPUTS:
        if raw.get(k) not in (None, ""):
            out[k] = str(raw[k])
    for k in _INT_INPUTS:
        if raw.get(k) not in (None, ""):
            out[k] = int(raw[k])
    for k in _FLOAT_INPUTS:
        if raw.get(k) not in (None, ""):
            out[k] = float(raw[k])
    if "first_time" in raw:
        out["first_time"] = bool(raw["first_time"]) and str(raw["first_time"]).lower() not in _FALSY
    return out


def _run_from_inputs(inputs: dict) -> dict:
    """Run a scenario from a saved/sanitized inputs dict (raises ValueError)."""
    return _run_scenario(
        price=float(inputs["price"]),
        down=str(inputs.get("down", "20%")),
        years=int(inputs.get("years", 30)),
        postal=str(inputs.get("postal", "M2J 0E8")),
        age=int(inputs.get("age", 35)),
        income=float(inputs.get("income", 120000.0)),
        strategy=str(inputs.get("strategy", "shelter-first")),
        first_time=bool(inputs.get("first_time", False)),
        rate=inputs.get("rate"), appreciation=inputs.get("appreciation"),
        rent=inputs.get("rent"), rent_growth=inputs.get("rent_growth"),
        property_tax_rate=inputs.get("property_tax_rate"),
        investment_return=inputs.get("investment_return"),
        insurance=float(inputs.get("insurance", 1500.0)),
        hoa=float(inputs.get("hoa", 0.0)),
        retirement_rate=inputs.get("retirement_rate"),
    )


def _snapshot(sc: dict) -> dict:
    """A light, recompute-free summary stored alongside a scenario."""
    sym = sc["sym"]
    return {
        "verdict_word": ("Buying" if sc["leader"] == "buyer" else "Renting"),
        "leader": sc["leader"],
        "gap_str": f'{sym}{abs(sc["gap"]):,.0f}',
        "after_tax": bool(sc["after_tax"]),
        "region": sc["params"]["region_label"],
        "postal": sc["params"]["postal_code"],
        "years": sc["years"],
    }


def _scenario_query(inputs: dict, sid: int | None = None) -> str:
    """Build the /evaluate query string that reopens a saved scenario."""
    from urllib.parse import urlencode
    q = {}
    for k, v in inputs.items():
        q[k] = str(v).lower() if isinstance(v, bool) else v
    if sid is not None:
        q["sid"] = sid
    return urlencode(q)


def _auth_enabled() -> bool:
    """Decide whether basic-auth is active, honouring the EVALUATOR_AUTH flag."""
    flag = os.environ.get("EVALUATOR_AUTH", "").strip().lower()
    if flag in _TRUTHY:
        if not _AUTH_PASS:
            raise RuntimeError(
                "EVALUATOR_AUTH is on but EVALUATOR_PASSWORD is not set — "
                "set a password or turn the flag off."
            )
        return True
    if flag in _FALSY:
        return False
    if flag:  # set to something we don't recognise
        raise RuntimeError(
            f"EVALUATOR_AUTH={flag!r} is not understood; use on/off (or leave unset)."
        )
    # Unset => auto: on only when a password is configured.
    return bool(_AUTH_PASS)


_AUTH_ON = _auth_enabled()


def require_auth(credentials: HTTPBasicCredentials | None = Depends(_security)) -> None:
    """Enforce basic-auth when enabled (see _auth_enabled); else no-op."""
    if not _AUTH_ON:
        return
    ok = credentials is not None and secrets.compare_digest(
        credentials.username, _AUTH_USER
    ) and secrets.compare_digest(credentials.password, _AUTH_PASS)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

# Sanity bounds so the public-facing handler can't be handed absurd work.
MAX_YEARS = 50
MAX_PRICE = 100_000_000


PAGE_HEAD = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Home Evaluator</title>
<style>
 :root{
   --brand:#1f77b4; --brand-dark:#155a8a; --brand-darker:#0f4468;
   --accent:#f4a836;
   --ink:#16263a; --muted:#5b6b7d; --line:#e2e8f0;
   --bg:#eef3f8; --card:#ffffff;
   --shadow:0 1px 2px rgba(16,40,66,.06),0 8px 24px rgba(16,40,66,.08);
   --radius:14px;
 }
 *{box-sizing:border-box}
 body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
   max-width:1000px;margin:0 auto;padding:1.5rem 1rem 3rem;color:var(--ink);
   line-height:1.55;-webkit-text-size-adjust:100%;
   background:
     radial-gradient(1100px 520px at 50% -280px,#dcebf7 0,rgba(220,235,247,0) 70%),
     linear-gradient(180deg,#f3f7fb 0,#eef3f8 100%);
   background-color:var(--bg);background-attachment:fixed;min-height:100vh}
 h1{font-size:1.55rem;line-height:1.25;margin:.2rem 0 1rem;letter-spacing:-.01em}
 h1.section{font-size:1.25rem;margin:.2rem 0 .4rem}
 p.lead{color:var(--muted);margin:.2rem 0 1rem;font-size:1.02rem}
 label{display:block;margin:0 0 .3rem;font-weight:600;font-size:.92rem;color:var(--ink)}
 .field{display:flex;flex-direction:column}
 .field small{color:var(--muted);font-size:.78rem;margin-top:.35rem;font-weight:400}
 /* 16px input font stops iOS Safari from auto-zooming on focus */
 input:not([type=range]):not([type=checkbox]),select{width:100%;padding:.7rem .8rem;border:1px solid #cfd9e4;
   border-radius:10px;font-size:16px;background:#fbfdff;color:var(--ink);
   transition:border-color .15s,box-shadow .15s,background .15s}
 input:not([type=range]):not([type=checkbox]):hover,select:hover{border-color:#aebfd2}
 input:not([type=range]):not([type=checkbox]):focus,select:focus{outline:none;border-color:var(--brand);
   box-shadow:0 0 0 3px rgba(31,119,180,.18);background:#fff}
 /* auto-fit collapses the form from 2 columns to 1 on narrow screens */
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1.1rem}
 button{margin-top:1.5rem;padding:.85rem 1.5rem;font-size:1.02rem;font-weight:600;
   border:0;border-radius:11px;color:#fff;cursor:pointer;width:100%;max-width:280px;
   background:linear-gradient(180deg,var(--brand) 0,var(--brand-dark) 100%);
   box-shadow:0 4px 14px rgba(31,119,180,.32);
   transition:transform .12s ease,box-shadow .12s ease,filter .12s ease}
 button:hover{filter:brightness(1.06);box-shadow:0 6px 20px rgba(31,119,180,.42);transform:translateY(-1px)}
 button:active{transform:translateY(0);box-shadow:0 3px 10px rgba(31,119,180,.3)}
 button:focus-visible{outline:3px solid rgba(31,119,180,.4);outline-offset:2px}
 img{width:100%;height:auto;display:block;margin:1rem 0;border:1px solid var(--line);
   border-radius:12px;box-shadow:var(--shadow);background:#fff}
 .card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
   padding:1.5rem 1.4rem;box-shadow:var(--shadow);margin:1.25rem 0}
 .summary{background:linear-gradient(180deg,#f3f8fc 0,#eef5fb 100%);
   border:1px solid #d3e2f0;border-radius:var(--radius);padding:1rem 1.2rem;margin:1.25rem 0;
   box-shadow:var(--shadow);line-height:1.65}
 .err{background:linear-gradient(180deg,#fdf4f0 0,#fbeee8 100%);border:1px solid #e6bcab;
   border-radius:var(--radius);padding:1rem 1.2rem;color:#8c3b1e;box-shadow:var(--shadow)}
 a{color:var(--brand);font-weight:500}
 a:hover{color:var(--brand-dark)}
 .back{display:inline-block;margin-top:.5rem}
 .disclaimer{color:var(--muted);font-size:.83rem;margin:1.6rem .2rem 0;line-height:1.5}

 /* ---- results page (classes used only by evaluate()'s output) ---- */
 .result-banner{display:flex;align-items:center;gap:1.4rem;flex-wrap:wrap;
   background:linear-gradient(135deg,var(--brand-darker) 0,var(--brand) 58%,#2b93d6 100%);
   color:#fff;border-radius:18px;padding:1.6rem 1.8rem;margin:.2rem 0 1.1rem;
   box-shadow:0 14px 36px rgba(15,68,104,.28);overflow:hidden;position:relative}
 .result-banner::after{content:"";position:absolute;right:-70px;top:-70px;
   width:220px;height:220px;border-radius:50%;pointer-events:none;
   background:radial-gradient(circle at 30% 30%,rgba(255,255,255,.18),rgba(255,255,255,0) 70%)}
 .rb-art{flex:0 0 96px;width:96px;position:relative;z-index:1}
 .rb-art svg{width:100%;height:auto;display:block;
   filter:drop-shadow(0 10px 16px rgba(8,40,64,.34));animation:floaty 6s ease-in-out infinite}
 .rb-body{flex:1 1 260px;min-width:0;position:relative;z-index:1}
 .rb-body h1{color:#fff;margin:.15rem 0 .5rem;font-size:1.5rem}
 .verdict{margin:0;font-size:1.05rem;color:#eaf4fc;display:flex;
   align-items:baseline;gap:.55rem;flex-wrap:wrap}
 .verdict .lead{font-weight:700;color:#fff}
 .v-amount{font-size:1.5rem;font-weight:800;color:var(--accent);letter-spacing:-.01em;line-height:1.1}
 .verdict .by{color:#cfe8fa;font-size:.92rem}

 .result-summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
   gap:.7rem;margin:0 0 1.4rem}
 .stat{background:var(--card);border:1px solid var(--line);border-radius:12px;
   padding:.75rem .9rem;box-shadow:var(--shadow)}
 .stat .k{display:block;font-size:.7rem;font-weight:700;letter-spacing:.06em;
   text-transform:uppercase;color:var(--muted);margin-bottom:.22rem}
 .stat .v{display:block;font-size:1.05rem;font-weight:700;color:var(--ink);letter-spacing:-.01em}
 .stat .v small{font-weight:600;color:var(--muted);font-size:.8rem}
 .live-badge{display:inline-block;margin-left:.4rem;padding:.05rem .4rem;border-radius:999px;
   font-size:.6rem;font-weight:700;letter-spacing:.05em;text-transform:uppercase;vertical-align:middle;
   color:#0a7d33;background:rgba(16,160,64,.14);border:1px solid rgba(16,160,64,.35)}
 .live-badge::before{content:"\\25CF";margin-right:.25rem;color:#10a040;font-size:.7em}

 .charts{margin-top:.2rem}
 .chart-card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
   padding:1.1rem 1.1rem 1.2rem;box-shadow:var(--shadow);margin:1.1rem 0;
   transition:transform .18s ease,box-shadow .18s ease}
 .chart-card:hover{transform:translateY(-3px);
   box-shadow:0 8px 20px rgba(16,40,66,.1),0 18px 44px rgba(16,40,66,.13)}
 .chart-head{display:flex;align-items:center;gap:.7rem;margin-bottom:.4rem}
 .chart-num{flex:0 0 auto;width:28px;height:28px;border-radius:8px;color:#fff;
   display:inline-flex;align-items:center;justify-content:center;font-weight:700;font-size:.86rem;
   background:linear-gradient(180deg,var(--brand) 0,var(--brand-dark) 100%);
   box-shadow:0 2px 6px rgba(31,119,180,.35)}
 .chart-title{font-size:1.05rem;font-weight:700;margin:0;letter-spacing:-.01em;line-height:1.25}
 .chart-cap{color:var(--muted);font-size:.86rem;margin:0 0 .75rem}
 .plot{width:100%;height:380px}
 @media (max-width:480px){ .plot{height:320px} }

 /* what-if sliders panel */
 .whatif{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
   padding:1.1rem 1.2rem;box-shadow:var(--shadow);margin:1.1rem 0}
 .whatif h2{font-size:1.05rem;margin:0 0 .15rem}
 .whatif .wf-sub{color:var(--muted);font-size:.84rem;margin:0 0 .9rem}
 .wf-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.7rem 1.4rem}
 .wf-row label{display:flex;justify-content:space-between;align-items:baseline;
   font-weight:600;font-size:.88rem;margin:0 0 .25rem}
 .wf-val{color:var(--brand);font-weight:700}
 input[type=range]{width:100%;accent-color:var(--brand);height:1.6rem;cursor:pointer;
   padding:0;margin:0;background:transparent;border:0}
 #wf-status{margin-left:.6rem;font-size:.8rem;color:var(--muted);font-weight:500;
   opacity:0;transition:opacity .15s}
 #wf-status.on{opacity:1}
 .wf-real{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;margin-top:1rem;
   padding-top:.9rem;border-top:1px solid var(--line);font-size:.88rem}
 .wf-real label{font-weight:600;display:flex;align-items:center;gap:.4rem;cursor:pointer}
 .wf-real input[type=checkbox]{width:1rem;height:1rem;accent-color:var(--brand);cursor:pointer}
 .wf-real input[type=number]{width:3.4rem;padding:.2rem .35rem;border:1px solid #cfd9e4;
   border-radius:7px;font-size:.85rem}
 .wf-real-infl{color:var(--muted)}
 .wf-dollars-note{color:var(--brand);font-weight:600;margin-left:auto}
 .charts{transition:opacity .2s}
 .charts.loading{opacity:.45}

 /* staggered entrance for results blocks */
 .reveal{opacity:0;animation:rise .6s cubic-bezier(.16,.84,.44,1) both}
 .result-banner.reveal{animation-delay:.04s}
 .result-summary.reveal{animation-delay:.12s}
 .charts .chart-card:nth-child(1){animation-delay:.20s}
 .charts .chart-card:nth-child(2){animation-delay:.30s}
 .charts .chart-card:nth-child(3){animation-delay:.40s}
 .charts .chart-card:nth-child(4){animation-delay:.50s}
 .charts .chart-card:nth-child(5){animation-delay:.60s}

 /* ---- landing hero (markup lives in FORM, not baked into PAGE_HEAD) ---- */
 .hero{display:flex;align-items:center;gap:1.6rem;flex-wrap:wrap;
   background:linear-gradient(135deg,var(--brand-darker) 0,var(--brand) 58%,#2b93d6 100%);
   color:#fff;border-radius:18px;padding:1.7rem 1.9rem;margin:.2rem 0 .4rem;
   box-shadow:0 14px 36px rgba(15,68,104,.28);overflow:hidden;position:relative}
 .hero-copy{flex:1 1 260px;min-width:0}
 .hero h1{color:#fff;margin:.1rem 0 .55rem;font-size:1.75rem}
 .hero p{margin:0;color:#e4f1fb;font-size:1.02rem;max-width:46ch}
 .eyebrow{display:inline-flex;align-items:center;gap:.5rem;font-size:.72rem;
   font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:#cfe8fa;
   background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);
   padding:.32rem .72rem;border-radius:999px;margin-bottom:.9rem}
 .eyebrow .dot{width:7px;height:7px;border-radius:50%;background:var(--accent);
   box-shadow:0 0 0 0 rgba(244,168,54,.7);animation:pulse 2.4s ease-out infinite}
 .hero-art{flex:0 0 220px;max-width:220px;width:100%}
 .hero-art svg{width:100%;height:auto;display:block;
   filter:drop-shadow(0 12px 18px rgba(8,40,64,.32));
   animation:floaty 6s ease-in-out infinite}

 /* inline-SVG illustration animations */
 .sun{transform-box:fill-box;transform-origin:center;animation:sunpulse 5s ease-in-out infinite}
 .cloud{animation:drift 16s linear infinite}
 .cloud.c2{animation-duration:22s;animation-delay:-7s}
 .keys{transform-box:fill-box;transform-origin:50% 6%;animation:sway 3.2s ease-in-out infinite}
 .win{animation:glow 4s ease-in-out infinite}
 .win.w2{animation-delay:1.3s}
 .win.w3{animation-delay:2.1s}

 @keyframes floaty{0%,100%{transform:translateY(0)}50%{transform:translateY(-7px)}}
 @keyframes sunpulse{0%,100%{transform:scale(1);opacity:.95}50%{transform:scale(1.08);opacity:1}}
 @keyframes drift{0%{transform:translateX(-34px)}100%{transform:translateX(300px)}}
 @keyframes sway{0%,100%{transform:rotate(-7deg)}50%{transform:rotate(7deg)}}
 @keyframes glow{0%,100%{opacity:.5}50%{opacity:1}}
 @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(244,168,54,.6)}
   70%{box-shadow:0 0 0 7px rgba(244,168,54,0)}100%{box-shadow:0 0 0 0 rgba(244,168,54,0)}}
 @keyframes rise{from{opacity:0;transform:translateY(18px)}to{opacity:1;transform:none}}

 @media (max-width:620px){
   .hero{padding:1.5rem 1.3rem;gap:1rem;text-align:center;justify-content:center}
   .hero-copy{flex-basis:100%}
   .hero p{margin:0 auto}
   .hero-art{flex-basis:165px;max-width:165px;margin:0 auto;order:-1}
   .result-banner{padding:1.4rem 1.3rem;gap:1rem;text-align:center;justify-content:center}
   .rb-body{flex-basis:100%}
   .rb-art{order:-1;flex-basis:78px;width:78px;margin:0 auto}
   .verdict{justify-content:center}
 }
 @media (max-width:480px){
   body{padding:1rem .8rem 2.5rem}
   .hero h1{font-size:1.42rem}
   .card{padding:1.2rem 1rem}
   button{width:100%;max-width:none}
   .rb-body h1{font-size:1.3rem}
   .v-amount{font-size:1.35rem}
   .chart-card{padding:1rem .9rem}
 }
 @media (prefers-reduced-motion:reduce){
   .hero-art svg,.sun,.cloud,.keys,.win,.eyebrow .dot,
   .rb-art svg,.reveal{animation:none}
   .reveal{opacity:1}
 }
 /* account sign-in widget (top-right) */
 .authbar{position:fixed;top:.8rem;right:.9rem;z-index:50;display:flex;
   align-items:center;gap:.6rem;font-size:.82rem}
 .authbar .who{color:var(--ink);background:var(--card);border:1px solid var(--line);
   padding:.3rem .7rem;border-radius:999px;font-weight:600;
   box-shadow:0 2px 8px rgba(8,40,64,.08)}
 .authbar .authbtn{display:inline-block;padding:.36rem .8rem;border-radius:999px;
   font-weight:700;text-decoration:none;color:#fff;background:var(--brand);
   border:1px solid rgba(8,40,64,.18);box-shadow:0 2px 8px rgba(8,40,64,.18)}
 .authbar .authbtn:hover{filter:brightness(1.07)}
 .authbar .authbtn.ghost{background:var(--card);color:var(--brand);
   border:1px solid var(--line)}
 @media (max-width:480px){ .authbar{top:.5rem;right:.5rem;font-size:.72rem;gap:.4rem}
   .authbar .who{display:none} }
 /* save-scenario panel (results page) */
 .savebox{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;
   background:var(--card);border:1px solid var(--line);border-radius:14px;
   padding:.9rem 1.1rem;margin:.2rem 0 .6rem;box-shadow:0 6px 18px rgba(8,40,64,.06)}
 .savebox h2{font-size:1rem;margin:0;color:var(--ink);flex:0 0 auto}
 .savebox input[type=text],.savebox button{box-sizing:border-box;font-family:inherit;margin:0;
   font-size:15px;line-height:1.2;padding:.55rem .9rem;border-radius:10px;border:1px solid transparent}
 .savebox input[type=text]{flex:1 1 200px;min-width:140px;border-color:var(--line);background:#fbfdff}
 .savebox button{flex:0 0 auto;cursor:pointer;font-weight:700;color:#fff;background:var(--brand)}
 .savebox button:hover{filter:brightness(1.07)}
 .savebox .save-status{display:inline-flex;align-items:center;align-self:stretch;
   box-sizing:border-box;border-radius:10px;font-size:.85rem;font-weight:600;color:var(--muted)}
 .savebox .save-status.ok{color:#0a7d33;background:rgba(16,160,64,.12);padding:0 .7rem}
 .savebox .save-status.err{color:#c0392b;background:rgba(192,57,43,.10);padding:0 .7rem}
 /* scenarios list page */
 .scen-list{display:grid;gap:.85rem;margin:1.2rem 0}
 .scen-card{display:flex;align-items:center;gap:1rem;flex-wrap:wrap;
   background:var(--card);border:1px solid var(--line);border-radius:14px;
   padding:1rem 1.2rem;box-shadow:0 4px 14px rgba(8,40,64,.05)}
 .scen-card .scen-main{flex:1 1 260px;min-width:0}
 .scen-card .scen-name{font-weight:700;color:var(--ink);font-size:1.05rem}
 .scen-card .scen-meta{color:var(--muted);font-size:.85rem;margin-top:.2rem}
 .scen-card .scen-verdict{font-weight:700}
 .scen-card .scen-actions{display:flex;gap:.5rem;flex-wrap:wrap}
 .scen-card .scen-actions a,.scen-card .scen-actions button{
   display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;margin:0;
   line-height:1;font-family:inherit;padding:.5rem .85rem;border-radius:9px;
   width:auto;min-width:5.25rem;
   font-size:.85rem;font-weight:700;text-decoration:none;cursor:pointer;
   border:1px solid var(--line);background:#fbfdff;color:var(--brand)}
 .scen-card .scen-form{display:inline-flex}
 .scen-card .scen-form button{width:100%}
 .scen-card .scen-actions .open{background:var(--brand);color:#fff;border-color:var(--brand)}
 .scen-card .scen-actions .del{color:#c0392b}
 .scen-empty{color:var(--muted);background:var(--card);border:1px dashed var(--line);
   border-radius:14px;padding:2rem;text-align:center;margin:1.2rem 0}
 .scen-form{display:inline}
 .scen-pick{width:18px;height:18px;flex:0 0 auto;margin:0;align-self:center;
   accent-color:var(--brand);cursor:pointer}
 /* compare bar + table */
 .cmp-bar{display:flex;align-items:center;justify-content:space-between;gap:1rem;
   flex-wrap:wrap;background:var(--card);border:1px solid var(--line);border-radius:12px;
   padding:.7rem 1rem;margin:1.2rem 0 .2rem}
 .cmp-bar span{color:var(--muted);font-size:.9rem;font-weight:600}
 .cmp-bar button{margin:0;padding:.5rem 1rem;border:0;border-radius:9px;cursor:pointer;
   font-weight:700;color:#fff;background:var(--brand)}
 .cmp-bar button:disabled{background:#aebfce;cursor:not-allowed}
 .cmp-wrap{overflow-x:auto;margin:1rem 0}
 .cmp-table{width:100%;border-collapse:collapse;background:var(--card);
   border:1px solid var(--line);border-radius:12px;overflow:hidden;font-size:.92rem}
 .cmp-table th,.cmp-table td{padding:.6rem .8rem;text-align:left;border-bottom:1px solid var(--line)}
 .cmp-table thead th{background:#f3f7fb;color:var(--ink);font-size:.85rem}
 .cmp-table tbody th{color:var(--muted);font-weight:600;white-space:nowrap}
 .cmp-table td{color:var(--ink);font-weight:600}
 /* shared (read-only) note + share box */
 .shared-note{background:#fff7e8;border:1px solid #f4d79a;color:#8a5a12;
   border-radius:12px;padding:.7rem 1rem;margin:.2rem 0 .6rem;font-size:.9rem;font-weight:600}
 .shared-note a{color:var(--brand-dark)}
 .sharebox .share-link{flex:1 1 240px;min-width:160px;font-family:ui-monospace,monospace;
   font-size:.85rem;color:var(--ink)}
 /* methodology / prose page */
 .prose{max-width:760px;margin:0 auto}
 .prose h2{font-size:1.25rem;color:var(--ink);margin:1.8rem 0 .5rem;
   padding-top:1.2rem;border-top:1px solid var(--line)}
 .prose h2:first-of-type{border-top:0;padding-top:0;margin-top:.6rem}
 .prose h3{font-size:.98rem;color:var(--ink);margin:1.1rem 0 .35rem}
 .prose p{color:#33435a;line-height:1.62;margin:.5rem 0}
 .prose ul{color:#33435a;line-height:1.6;padding-left:1.2rem;margin:.5rem 0}
 .prose li{margin:.32rem 0}
 .prose a{color:var(--brand-dark);word-break:break-word}
 .prose code{background:#eef3f8;padding:.1rem .35rem;border-radius:5px;font-size:.86em}
 /* global footer / disclaimer */
 .site-foot{max-width:1000px;margin:2.6rem auto 0;padding:1.3rem 1rem 0;
   border-top:1px solid var(--line);color:var(--muted);font-size:.85rem;line-height:1.55}
 .site-foot p{margin:.4rem 0}
 .site-foot a{color:var(--brand-dark);font-weight:600}
 /* admin dashboard */
 .admin-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
   gap:1rem;margin:1.2rem 0}
 .admin-card{background:var(--card);border:1px solid var(--line);border-radius:14px;
   padding:1.1rem 1.2rem;box-shadow:0 4px 14px rgba(8,40,64,.05)}
 .admin-card h2{font-size:1rem;color:var(--ink);margin:0 0 .8rem}
 .bar-row{display:flex;align-items:center;gap:.6rem;margin:.4rem 0;font-size:.88rem}
 .bar-label{flex:0 0 38%;color:var(--ink);font-weight:600;overflow:hidden;
   text-overflow:ellipsis;white-space:nowrap}
 .bar-track{flex:1 1 auto;height:9px;background:#eef3f8;border-radius:999px;overflow:hidden}
 .bar-fill{display:block;height:100%;background:var(--brand);border-radius:999px}
 .bar-num{flex:0 0 auto;color:var(--muted);font-weight:600;min-width:64px;text-align:right}
 .bar-num small{font-weight:500}
</style></head><body>"""

PAGE_FOOT = (
    '<footer class="site-foot">'
    '<p><strong>Not financial advice.</strong> This is an educational estimate based on '
    'long-run historical assumptions and the numbers you enter &mdash; real outcomes will '
    'differ, and past investment returns do not guarantee future results. Before making a '
    'decision, consult a licensed financial advisor, mortgage broker, and tax professional.</p>'
    '<p><a href="/methodology">How it works, assumptions &amp; data sources &rarr;</a></p>'
    '</footer></body></html>'
)

# What-if sliders behaviour. BASE (the fixed inputs) is injected per-result; this
# is the static part. Sliders update their value labels instantly and, debounced,
# call /api/recompute to re-render all charts + the headline numbers in place.
WHATIF_SCRIPT_BODY = """
var WF_T=null;
function wfFmtPct(v){return v+'%';}
function wfFmtMoney(v){return '$'+(+v).toLocaleString();}
var WF_SLIDERS={
  'down':{val:'wf-down-val',fmt:wfFmtPct},
  'rate':{val:'wf-rate-val',fmt:wfFmtPct},
  'appr':{val:'wf-appr-val',fmt:wfFmtPct},
  'ret':{val:'wf-ret-val',fmt:wfFmtPct},
  'rent':{val:'wf-rent-val',fmt:wfFmtMoney}
};
function wfLabels(){
  for(var k in WF_SLIDERS){
    var el=document.getElementById('s-'+k);
    if(el) document.getElementById(WF_SLIDERS[k].val).textContent=WF_SLIDERS[k].fmt(el.value);
  }
}
function wfStatus(on){var s=document.getElementById('wf-status');if(s)s.classList.toggle('on',on);
  var c=document.getElementById('charts');if(c)c.classList.toggle('loading',on);}
function wfSet(id,htmlVal){var e=document.getElementById(id);if(e&&htmlVal!=null)e.innerHTML=htmlVal;}
function wfDollarsNote(){
  var el=document.getElementById('wf-dollars-note'); if(!el) return;
  var r=document.getElementById('wf-real'), inf=document.getElementById('wf-infl');
  el.textContent=(r&&r.checked)
    ? '\\u2713 today\\u2019s dollars ('+(inf?inf.value:2)+'%/yr)'
    : 'future (nominal) dollars';
}
function wfRenewNote(){
  var el=document.getElementById('wf-renew-note'); if(!el) return;
  var r=document.getElementById('wf-renew'), rr=document.getElementById('wf-renew-rate');
  el.textContent=(r&&r.checked)
    ? '\\u2713 renews at '+(rr?rr.value:5.5)+'% every 5 yrs'
    : 'rate fixed for the full term';
}
function wfRecompute(){
  var q=new URLSearchParams(BASE);
  q.set('down', document.getElementById('s-down').value+'%');
  q.set('rate', (document.getElementById('s-rate').value/100));
  q.set('appreciation', (document.getElementById('s-appr').value/100));
  q.set('investment_return', (document.getElementById('s-ret').value/100));
  q.set('rent', document.getElementById('s-rent').value);
  var rc=document.getElementById('wf-real');
  if(rc&&rc.checked){q.set('real','true');
    var inf=document.getElementById('wf-infl'); if(inf) q.set('inflation', (inf.value/100));}
  var rn=document.getElementById('wf-renew');
  if(rn&&rn.checked){q.set('renewals','true');
    var rr=document.getElementById('wf-renew-rate'); if(rr) q.set('renewal_rate', (rr.value/100));}
  wfDollarsNote(); wfRenewNote();
  wfStatus(true);
  fetch('/api/recompute?'+q.toString()).then(function(r){return r.json();}).then(function(d){
    wfStatus(false);
    if(!d.ok){wfSet('wf-by', d.error||'Could not update'); return;}
    if(d.chart_data) renderCharts(d.chart_data);
    wfSet('wf-verdict', d.verdict_word);
    wfSet('wf-gap', d.gap_str);
    wfSet('wf-by', d.by_str);
    var s=d.stats||{};
    wfSet('wf-down', s.down); wfSet('wf-mortgage', s.mortgage);
    wfSet('wf-crossover', s.crossover); wfSet('wf-renter-tax', s.renter_tax);
    wfSet('wf-buy-costs', s.buy_costs); wfSet('wf-sell-costs', s.sell_costs);
  }).catch(function(){wfStatus(false);});
}
function wfOnInput(){wfLabels();clearTimeout(WF_T);WF_T=setTimeout(wfRecompute,350);}
['down','rate','appr','ret','rent'].forEach(function(k){
  var el=document.getElementById('s-'+k); if(el) el.addEventListener('input', wfOnInput);
});
var wfRealEl=document.getElementById('wf-real');
if(wfRealEl) wfRealEl.addEventListener('change', function(){wfDollarsNote();wfRecompute();});
var wfInflEl=document.getElementById('wf-infl');
if(wfInflEl) wfInflEl.addEventListener('input', function(){
  wfDollarsNote();
  if(document.getElementById('wf-real').checked){clearTimeout(WF_T);WF_T=setTimeout(wfRecompute,350);}
});
var wfRenewEl=document.getElementById('wf-renew');
if(wfRenewEl) wfRenewEl.addEventListener('change', function(){wfRenewNote();wfRecompute();});
var wfRenewRate=document.getElementById('wf-renew-rate');
if(wfRenewRate) wfRenewRate.addEventListener('input', function(){
  wfRenewNote();
  if(document.getElementById('wf-renew').checked){clearTimeout(WF_T);WF_T=setTimeout(wfRecompute,350);}
});
wfLabels();
wfDollarsNote(); wfRenewNote();
"""

# Save + share behaviour. RESULT_INPUTS (+ SCEN_ID) are injected per result;
# currentInputs() folds in the live slider state so tweaks are captured faithfully.
RESULT_SCRIPT_BODY = """
function currentInputs(){
  var inp=Object.assign({}, RESULT_INPUTS);
  var g=function(id){var e=document.getElementById(id);return e?e.value:null;};
  if(g('s-down')!=null) inp.down=g('s-down')+'%';
  if(g('s-rate')!=null) inp.rate=g('s-rate')/100;
  if(g('s-appr')!=null) inp.appreciation=g('s-appr')/100;
  if(g('s-ret')!=null) inp.investment_return=g('s-ret')/100;
  if(g('s-rent')!=null) inp.rent=g('s-rent');
  return inp;
}
function saveScenario(){
  var ne=document.getElementById('scen-name');
  var name=((ne&&ne.value)||'').trim();
  var st=document.getElementById('save-status');
  if(!name){st.className='save-status err';st.textContent='Name it first';return;}
  st.className='save-status';st.textContent='Saving\\u2026';
  fetch('/api/scenarios',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:name,inputs:currentInputs(),id:SCEN_ID})})
   .then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
   .then(function(res){
     if(res.ok&&res.d.ok){st.className='save-status ok';st.textContent='Saved \\u2713';
       SCEN_ID=res.d.id;document.getElementById('save-btn').textContent='Update scenario';}
     else{st.className='save-status err';st.textContent=(res.d.detail||'Could not save');}
   }).catch(function(){st.className='save-status err';st.textContent='Could not save';});
}
function shareScenario(){
  var st=document.getElementById('share-status');
  st.className='save-status';st.textContent='Creating link\\u2026';
  fetch('/api/share',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({inputs:currentInputs()})})
   .then(function(r){return r.json();})
   .then(function(d){
     if(d.ok){var url=location.origin+d.url;var box=document.getElementById('share-link');
       box.value=url;box.style.display='';box.focus();box.select();
       if(navigator.clipboard) navigator.clipboard.writeText(url);
       st.className='save-status ok';st.textContent='Link copied \\u2713';}
     else{st.className='save-status err';st.textContent=(d.detail||'Could not create link');}
   }).catch(function(){st.className='save-status err';st.textContent='Could not create link';});
}
"""

# Compare-page chart: one "buyer minus renter" net-worth-gap line per scenario.
COMPARE_SCRIPT_BODY = """
(function(){
  if(typeof Plotly==='undefined'||typeof CMP==='undefined') return;
  var colors=['#1f77b4','#ff7f0e','#2ca02c','#9467bd'];
  var traces=CMP.map(function(s,i){
    var gap=s.buyer.map(function(b,j){return b-s.renter[j];});
    return {x:s.years,y:gap,name:s.name,mode:'lines',
      line:{color:colors[i%colors.length],width:2.6},
      hovertemplate:'%{y:$,.0f}<extra>'+s.name+'</extra>'};
  });
  var layout={margin:{l:64,r:16,t:10,b:42},hovermode:'x unified',
    paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',
    font:{family:'system-ui,-apple-system,Segoe UI,Roboto,sans-serif',size:12,color:'#16263a'},
    legend:{orientation:'h',y:1.14,x:0,font:{size:11}},
    xaxis:{title:'Year',gridcolor:'#eef2f7',zeroline:false,automargin:true},
    yaxis:{title:'Buyer \\u2212 renter ($)',tickprefix:'$',tickformat:'.2s',
      gridcolor:'#eef2f7',zeroline:true,zerolinecolor:'#b9c4d0',automargin:true}};
  Plotly.newPlot('cmp-chart',traces,layout,
    {responsive:true,displaylogo:false,modeBarButtonsToRemove:['lasso2d','select2d']});
})();
"""

# Interactive chart rendering with the vendored Plotly. renderCharts(d) is global
# so the what-if sliders can re-render after /api/recompute. Hover tooltips,
# clickable legend (toggle series), and box-zoom/reset come from Plotly for free.
CHARTS_SCRIPT_BODY = """
var C_FONT={family:'system-ui,-apple-system,Segoe UI,Roboto,sans-serif',size:12,color:'#16263a'};
var C_CFG={responsive:true,displaylogo:false,scrollZoom:false,
  modeBarButtonsToRemove:['lasso2d','select2d','autoScale2d','toggleSpikelines']};
var C_HT='%{y:$,.0f}<extra>%{fullData.name}</extra>';
function cMoneyAxis(t){return {title:t,tickprefix:'$',tickformat:'.2s',gridcolor:'#eef2f7',zeroline:false,automargin:true};}
function cLayout(extra){
  var b={margin:{l:62,r:16,t:10,b:42},hovermode:'x unified',
    paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',font:C_FONT,
    legend:{orientation:'h',y:1.14,x:0,font:{size:11}},
    xaxis:{title:'Year',gridcolor:'#eef2f7',zeroline:false,automargin:true}};
  for(var k in (extra||{})) b[k]=extra[k];
  return b;
}
function cNearest(arr,v){var bi=-1,bd=1e18;for(var i=0;i<arr.length;i++){var dd=Math.abs(arr[i]-v);if(dd<bd){bd=dd;bi=i;}}return bi;}
function cLine(x,y,name,color,opt){var t={x:x,y:y,name:name,mode:'lines',line:{color:color,width:(opt&&opt.w)||2.4},hovertemplate:C_HT};
  if(opt&&opt.fill){t.fill=opt.fill;t.fillcolor=opt.fillcolor;} return t;}
function renderCharts(d){
  if(typeof Plotly==='undefined') return;
  var x=d.years;
  Plotly.react('chart-1',[
    cLine(x,d.c1.home_value,'Home value','#1f77b4',{w:2.6}),
    cLine(x,d.c1.loan_balance,'Mortgage balance','#d62728'),
    cLine(x,d.c1.equity,'Equity','#2ca02c',{fill:'tozeroy',fillcolor:'rgba(44,160,44,0.08)'})
  ],cLayout({yaxis:cMoneyAxis('Value')}),C_CFG);

  function area(y,name,color){return {x:x,y:y,name:name,mode:'lines',stackgroup:'c2',
    line:{width:0.5,color:color},fillcolor:color,hovertemplate:C_HT};}
  Plotly.react('chart-2',[
    area(d.c2.down,'Down payment','rgba(140,86,75,0.80)'),
    area(d.c2.principal,'Principal','rgba(44,160,44,0.75)'),
    area(d.c2.interest,'Interest','rgba(214,39,40,0.72)'),
    area(d.c2.property_tax,'Property tax','rgba(148,103,189,0.72)'),
    area(d.c2.carry,'Insurance/HOA/maint.','rgba(255,127,14,0.72)')
  ],cLayout({yaxis:cMoneyAxis('Cumulative cost')}),C_CFG);

  Plotly.react('chart-3',[
    cLine(x,d.c3.contributions,'Contributions','#8c564b',{w:1.6,fill:'tozeroy',fillcolor:'rgba(140,86,75,0.10)'}),
    cLine(x,d.c3.portfolio,'Portfolio value','#1f77b4',{w:2.6,fill:'tonexty',fillcolor:'rgba(31,119,180,0.12)'})
  ],cLayout({yaxis:cMoneyAxis('Value')}),C_CFG);

  Plotly.react('chart-4',[
    cLine(x,d.c4.contributions,'Contributions','#8c564b',{w:1.6,fill:'tozeroy',fillcolor:'rgba(140,86,75,0.10)'}),
    cLine(x,d.c4.portfolio,'Portfolio value','#ff7f0e',{w:2.6,fill:'tonexty',fillcolor:'rgba(255,127,14,0.12)'})
  ],cLayout({yaxis:cMoneyAxis('Value')}),C_CFG);

  Plotly.react('chart-5',[
    cLine(x,d.c5.buyer,'Homeowner (after tax)','#1f77b4',{w:2.8}),
    cLine(x,d.c5.renter,'Renter (after tax)','#ff7f0e',{w:2.8})
  ],cLayout({yaxis:cMoneyAxis('Net worth')}),C_CFG);

  if(d.c6 && d.c6.grid && d.c6.grid.length){
    var shapes=[],anns=[];
    var bi=cNearest(d.c6.appr,d.c6.base_appr), bj=cNearest(d.c6.ret,d.c6.base_ret);
    if(bi>=0&&bj>=0){
      shapes.push({type:'rect',x0:d.c6.ret[bj]-0.5,x1:d.c6.ret[bj]+0.5,
        y0:d.c6.appr[bi]-0.5,y1:d.c6.appr[bi]+0.5,line:{color:'#111',width:2.2}});
      anns.push({x:d.c6.ret[bj],y:d.c6.appr[bi],text:'your scenario',showarrow:false,
        font:{size:9,color:'#111'},yshift:-1});
    }
    Plotly.react('chart-6',[{
      type:'heatmap',x:d.c6.ret,y:d.c6.appr,z:d.c6.grid,colorscale:'RdYlGn',zmid:0,
      hovertemplate:'appreciation %{y}% &middot; return %{x}%<br>gap %{z:$,.0f}<extra></extra>',
      colorbar:{tickprefix:'$',tickformat:'.2s',thickness:12}
    }],cLayout({shapes:shapes,annotations:anns,hovermode:'closest',
      xaxis:{title:'Investment return %',dtick:1,zeroline:false},
      yaxis:{title:'Home appreciation %',dtick:1,zeroline:false}}),C_CFG);
  }
}
if(typeof CHART_DATA!=='undefined') renderCharts(CHART_DATA);
"""

# Compact home-themed badge for the results banner. Mirrors the landing hero
# motif (sun/house/window) and reuses the shared .sun/.win animation classes.
RESULT_BADGE_SVG = """
<svg viewBox="0 0 100 100" role="img" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="rsky" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#bfe3fb"/><stop offset="1" stop-color="#eef8ff"/>
    </linearGradient>
    <linearGradient id="rroof" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#2b93d6"/><stop offset="1" stop-color="#155a8a"/>
    </linearGradient>
    <linearGradient id="rsun" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffd76a"/><stop offset="1" stop-color="#f4a836"/>
    </linearGradient>
    <linearGradient id="rwall" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#eef3f8"/>
    </linearGradient>
    <clipPath id="rpanel"><rect x="6" y="6" width="88" height="88" rx="22"/></clipPath>
  </defs>
  <rect x="6" y="6" width="88" height="88" rx="22" fill="url(#rsky)"/>
  <g clip-path="url(#rpanel)">
    <circle class="sun" cx="28" cy="27" r="11" fill="url(#rsun)"/>
    <rect x="6" y="73" width="88" height="21" fill="#d7ead9"/>
    <rect x="14" y="60" width="16" height="14" rx="2" fill="#cdddec"/>
    <rect x="34" y="44" width="40" height="30" fill="url(#rwall)" stroke="#dbe5ef"/>
    <polygon points="28,46 54,25 80,46" fill="url(#rroof)"/>
    <rect x="48" y="57" width="12" height="17" rx="1.5" fill="#f4a836"/>
    <circle cx="57.5" cy="66" r="1.1" fill="#8a5a12"/>
    <rect class="win" x="38" y="50" width="9" height="9" rx="1.5" fill="#bfe3fb" stroke="#cfe0ef" stroke-width="1.5"/>
    <rect class="win w2" x="61" y="50" width="9" height="9" rx="1.5" fill="#bfe3fb" stroke="#cfe0ef" stroke-width="1.5"/>
  </g>
</svg>"""

FORM = PAGE_HEAD + """
<section class="hero">
  <div class="hero-copy">
    <span class="eyebrow"><span class="dot"></span> Canadian Home Finance</span>
    <h1>Buy&#8209;vs&#8209;Rent Home Evaluator</h1>
    <p>See whether buying or renting builds more wealth over your mortgage &mdash;
       six interactive charts from one scenario.</p>
  </div>
  <div class="hero-art" aria-hidden="true">
    <svg viewBox="0 0 240 220" role="img" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#cdeafd"/><stop offset="1" stop-color="#eef8ff"/>
        </linearGradient>
        <linearGradient id="sun" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#ffd76a"/><stop offset="1" stop-color="#f4a836"/>
        </linearGradient>
        <linearGradient id="roof" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#2b93d6"/><stop offset="1" stop-color="#155a8a"/>
        </linearGradient>
        <linearGradient id="wall" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#eef3f8"/>
        </linearGradient>
        <clipPath id="panel"><rect x="10" y="10" width="220" height="200" rx="26"/></clipPath>
      </defs>

      <rect x="10" y="10" width="220" height="200" rx="26" fill="url(#sky)"/>
      <g clip-path="url(#panel)">
        <!-- sun -->
        <circle class="sun" cx="56" cy="54" r="19" fill="url(#sun)"/>
        <!-- drifting clouds -->
        <g class="cloud" fill="#ffffff" opacity=".9">
          <ellipse cx="120" cy="46" rx="20" ry="12"/>
          <ellipse cx="104" cy="52" rx="13" ry="9"/>
          <ellipse cx="136" cy="52" rx="14" ry="9"/>
        </g>
        <g class="cloud c2" fill="#ffffff" opacity=".75">
          <ellipse cx="40" cy="96" rx="16" ry="9"/>
          <ellipse cx="54" cy="100" rx="11" ry="7"/>
        </g>
        <!-- neighbourhood skyline -->
        <rect x="20" y="150" width="34" height="50" rx="3" fill="#cdddec"/>
        <rect x="186" y="138" width="36" height="62" rx="3" fill="#cdddec"/>
        <rect x="168" y="160" width="22" height="40" rx="3" fill="#bcd2e6"/>
        <!-- ground -->
        <rect x="10" y="186" width="220" height="24" fill="#d7ead9"/>
        <!-- house -->
        <rect x="150" y="92" width="13" height="26" fill="#155a8a"/>
        <rect x="90" y="120" width="76" height="66" fill="url(#wall)" stroke="#dbe5ef"/>
        <polygon points="80,122 128,84 176,122" fill="url(#roof)"/>
        <!-- door -->
        <rect x="116" y="150" width="22" height="36" rx="2" fill="#f4a836"/>
        <circle cx="133" cy="168" r="2" fill="#8a5a12"/>
        <!-- windows -->
        <g stroke="#cfe0ef" stroke-width="2">
          <rect class="win" x="97" y="132" width="19" height="19" rx="2" fill="#bfe3fb"/>
          <rect class="win w2" x="142" y="132" width="19" height="19" rx="2" fill="#bfe3fb"/>
        </g>
        <!-- hanging keys -->
        <g class="keys">
          <line x1="200" y1="22" x2="200" y2="40" stroke="#9aa7b4" stroke-width="2"/>
          <circle cx="200" cy="50" r="9" fill="none" stroke="url(#sun)" stroke-width="4"/>
          <rect x="198" y="58" width="4" height="24" fill="#f4a836"/>
          <rect x="202" y="72" width="6" height="3.5" fill="#f4a836"/>
          <rect x="202" y="78" width="6" height="3.5" fill="#f4a836"/>
        </g>
      </g>
    </svg>
  </div>
</section>

<div class="card">
  <h1 class="section">Run a scenario</h1>
  <p class="lead">Enter the details below to generate your comparison charts.</p>
  <form action="/evaluate" method="get">
    <div class="grid">
      <div class="field">
        <label for="price">House price ($)</label>
        <input id="price" name="price" value="1000000" inputmode="numeric">
        <small>Total purchase price.</small>
      </div>
      <div class="field">
        <label for="down">Down payment ($ or %)</label>
        <input id="down" name="down" value="200000">
        <small>A dollar amount or a percent, e.g. 20%.</small>
      </div>
      <div class="field">
        <label for="years">Mortgage term (years)</label>
        <input id="years" name="years" value="30" inputmode="numeric">
        <small>Amortization length.</small>
      </div>
      <div class="field">
        <label for="address">Find by address (optional)</label>
        <div style="display:flex;gap:.4rem;align-items:stretch">
          <input id="address" placeholder="e.g. 290 Bremner Blvd, Toronto" style="flex:1;min-width:0">
          <button type="button" onclick="geocodeAddr()"
            style="flex:none;width:auto;max-width:none;margin-top:0;padding:0 1.1rem;font-size:.95rem;box-shadow:none">Find</button>
        </div>
        <small id="addr_status">Auto-fills the postal code from a street address.</small>
      </div>
      <div class="field">
        <label for="postal">Postal code</label>
        <input id="postal" name="postal" value="M2J 0E8">
        <small>Sets regional rates, land-transfer tax &amp; rent.</small>
      </div>
      <div class="field">
        <label for="age">Your age</label>
        <input id="age" name="age" value="35" inputmode="numeric">
        <small>Sets your TFSA contribution room.</small>
      </div>
      <div class="field">
        <label for="income">Annual income ($)</label>
        <input id="income" name="income" value="120000" inputmode="numeric">
        <small>Sets RRSP room &amp; your tax rate.</small>
      </div>
      <div class="field">
        <label for="strategy">Investing strategy</label>
        <select id="strategy" name="strategy">
          <option value="shelter-first" selected>Use TFSA &amp; RRSP first, then taxable</option>
          <option value="taxable-only">Taxable account only</option>
        </select>
        <small>Where the renter's savings go.</small>
      </div>
      <div class="field">
        <label for="first_time">First-time buyer?</label>
        <select id="first_time" name="first_time">
          <option value="false" selected>No</option>
          <option value="true">Yes</option>
        </select>
        <small>Applies land-transfer-tax rebates.</small>
      </div>
    </div>
    <button type="submit">Evaluate scenario</button>
  </form>
</div>

<p class="disclaimer">
  Projections use long-run historical assumptions and are not financial advice.
</p>
<script>
function geocodeAddr(){
  var a=document.getElementById('address').value.trim();
  var s=document.getElementById('addr_status');
  if(a.length<3){s.textContent='Enter an address first.';return;}
  s.textContent='Looking up…';
  fetch('/api/geocode?q='+encodeURIComponent(a)).then(function(r){return r.json();}).then(function(d){
    if(d&&d.ok){
      document.getElementById('postal').value=d.postal;
      s.textContent='Found '+d.postal+(d.region?' → '+d.region:'');
    }else{
      s.textContent='No match — enter the postal code manually.';
    }
  }).catch(function(){s.textContent='Lookup failed — enter the postal code manually.';});
}
</script>
""" + PAGE_FOOT


def _error_page(message: str) -> HTMLResponse:
    body = (
        PAGE_HEAD
        + f'<h1>Home Evaluator</h1><div class="err">{html.escape(message)}</div>'
        + '<p><a class="back" href="/">&larr; Back to the form</a></p>'
        + PAGE_FOOT
    )
    return HTMLResponse(body, status_code=400)


def _chart_data(projection: dict, params: dict) -> dict:
    """Extract the plottable series from a projection into a JSON-able dict.

    The browser renders these with Plotly (interactive: hover, legend toggle,
    zoom) — the server no longer rasterizes charts for the web. (The CLI still
    uses matplotlib via evaluator.charts.)
    """
    yrs = projection["years"].tolist()
    sens = projection.get("sensitivity") or {}

    def col(key):
        return projection[key].tolist()

    down_payment = float(params["down_payment"])
    return {
        "years": yrs,
        "term": int(params["term_years"]),
        "c1": {
            "home_value": col("home_value"),
            "loan_balance": col("loan_balance"),
            "equity": col("equity"),
        },
        "c2": {
            "down": [down_payment] * len(yrs),
            "principal": col("cum_principal"),
            "interest": col("cum_interest"),
            "property_tax": col("cum_property_tax"),
            "carry": col("cum_insurance_hoa"),
        },
        "c3": {
            "portfolio": col("renter_portfolio"),
            "contributions": col("renter_contributions"),
        },
        "c4": {
            "portfolio": col("owner_adv_portfolio"),
            "contributions": col("owner_adv_contributions"),
        },
        "c5": {
            "buyer": col("buyer_net_worth_after_tax"),
            "renter": col("renter_net_worth_after_tax"),
        },
        "c6": {
            "appr": (sens["appreciation_values"] * 100).tolist() if sens else [],
            "ret": (sens["return_values"] * 100).tolist() if sens else [],
            "grid": sens["gap_grid"].tolist() if sens else [],
            "base_appr": float(sens.get("base_appreciation", 0)) * 100 if sens else 0,
            "base_ret": float(sens.get("base_return", 0)) * 100 if sens else 0,
        },
    }


def _run_scenario(
    *, price, down, years, postal, age, income, strategy, first_time,
    rate=None, appreciation=None, rent=None, rent_growth=None,
    property_tax_rate=None, investment_return=None, insurance=1500.0, hoa=0.0,
    retirement_rate=None, show_real=False, inflation=None,
    renewals=False, renewal_rate=None,
) -> dict:
    """Validate inputs, run the full pipeline, build the chart data.

    Shared by the HTML results page and the JSON /api/recompute endpoint. Raises
    ValueError (with a user-facing message) on bad input; otherwise returns a
    dict with the JSON chart data (for Plotly) + the derived display values.
    """
    if not (0 < price <= MAX_PRICE):
        raise ValueError(f"Price must be between $1 and ${MAX_PRICE:,.0f}.")
    if not (1 <= years <= MAX_YEARS):
        raise ValueError(f"Mortgage term must be between 1 and {MAX_YEARS} years.")
    try:
        down_amount = cli._parse_down_payment(down, price)
    except ValueError:
        raise ValueError(f"Could not read down payment '{down}'. Use e.g. 200000 or 20%.")
    if not (0 < down_amount < price):
        raise ValueError("Down payment must be greater than $0 and less than the price.")
    if not (18 <= age <= 100):
        raise ValueError("Age must be between 18 and 100.")
    if not (0 <= income <= 100_000_000):
        raise ValueError("Annual income must be $0 or more.")
    if strategy not in ("shelter-first", "taxable-only"):
        raise ValueError("Investing strategy must be 'shelter-first' or 'taxable-only'.")
    cmhc_check = tax.cmhc_insurance(price, down_amount)
    if cmhc_check["required"] and not cmhc_check["insurable"]:
        raise ValueError(f"Down payment is too low: {cmhc_check['reason']}.")

    args = argparse.Namespace(
        price=price, down=down, years=years, postal=postal,
        age=age, income=income, account_strategy=strategy, retirement_rate=retirement_rate,
        first_time_buyer=first_time, commission_rate=None, purchase_legal=None,
        no_transaction_costs=False, live=_LIVE_DATA,
        rate=rate, appreciation=appreciation, rent=rent, rent_growth=rent_growth,
        property_tax_rate=property_tax_rate, investment_return=investment_return,
        insurance=insurance, hoa=hoa, out=None, no_charts=False,
        renewals=renewals, renewal_rate=renewal_rate, rate_term=None,
    )
    real_inflation = (projections.DEFAULT_INFLATION if inflation is None else float(inflation))
    try:
        params = cli.build_engine_params(args)
        projection = projections.build_projection(params)
        projection["sensitivity"] = projections.build_sensitivity(params)
        # Optional today's-dollars view: deflate before summary + chart data.
        if show_real:
            projection = projections.deflate_projection(projection, real_inflation)
            params["show_real"] = True
            params["real_inflation"] = real_inflation
        summary = projections.compute_summary(projection, params)
        chart_data = _chart_data(projection, params)
    except Exception as exc:  # noqa: BLE001 - surface any modelling error to the user
        raise ValueError(f"Could not evaluate this scenario: {exc}")

    sym = params["currency_symbol"]
    cy = summary.get("crossover_year")
    cross = f"~ year {cy:.1f}" if cy else "Not within term"
    gap = summary["final_buyer_minus_renter"]
    leader = "buyer" if gap >= 0 else "renter"
    verdict_word = "Buying" if leader == "buyer" else "Renting"
    pct = params["down_payment"] / params["purchase_price"] * 100
    after_tax = bool(summary.get("after_tax"))
    strat_label = ("TFSA & RRSP first" if summary.get("account_strategy") == "shelter-first"
                   else "Taxable only")

    return {
        "chart_data": chart_data,
        "summary": summary,
        "params": params,
        "sym": sym,
        "cross": cross,
        "gap": gap,
        "leader": leader,
        "verdict_word": verdict_word,
        "pct": pct,
        "after_tax": after_tax,
        "strat_label": strat_label,
        "years": int(years),
        "show_real": bool(show_real),
        "real_inflation": real_inflation,
        "renewals": bool(summary.get("renewals_enabled")),
        "renewal_rate": summary.get("renewal_rate"),
    }


def _buy_costs_field(sym: str, summary: dict, params: dict) -> str:
    """Up-front purchase-cost tile, breaking out CMHC when the loan is high-ratio."""
    pc = summary.get("purchase_closing_costs", 0)
    cmhc = params.get("cmhc") or {}
    if cmhc.get("required"):
        pst = cmhc.get("pst", 0.0)
        note = (f'(land-transfer + legal + {sym}{pst:,.0f} CMHC PST; '
                f'+{sym}{cmhc.get("premium", 0):,.0f} premium financed @ {cmhc.get("rate", 0) * 100:.2f}%)')
        return f'{sym}{pc - pst:,.0f} <small>{note}</small>'
    return f'{sym}{pc:,.0f} <small>(land-transfer + legal)</small>'


def _result_fields(sc: dict) -> dict:
    """Build the dynamic display strings (verdict, gap, stat tiles).

    Shared so the initial page and /api/recompute render identical markup.
    """
    summary = sc["summary"]
    params = sc["params"]
    sym = sc["sym"]
    years = sc["years"]
    tax_tag = " after tax" if sc["after_tax"] else ""
    lm = params.get("live_meta")
    rate_is_live = lm and abs(params["mortgage_rate"] - lm["rate"]) < 1e-9
    rate_badge = (f' <span class="live-badge" title="Bank of Canada, as of {html.escape(lm["as_of"])}">live</span>'
                  if rate_is_live else "")
    mortgage_str = f'{years} yr <small>@ {params["mortgage_rate"] * 100:.2f}%</small>{rate_badge}'
    if summary.get("renewals_enabled"):
        mortgage_str += (f' <small>&rarr; renews {summary.get("renewal_rate", 0) * 100:.2f}% '
                         f'({sym}{summary.get("mortgage_payment", 0):,.0f}&rarr;'
                         f'{sym}{summary.get("renewal_payment", 0):,.0f}/mo)</small>')
    stats = {
        "down": f'{sym}{params["down_payment"]:,.0f} <small>({sc["pct"]:.0f}%)</small>',
        "mortgage": mortgage_str,
        "crossover": sc["cross"],
        "renter_tax": f'{sym}{summary.get("renter_tax_paid", 0):,.0f}',
        "buy_costs": _buy_costs_field(sym, summary, params),
        "sell_costs": (f'{sym}{summary.get("selling_costs_final", 0):,.0f} '
                       '<small>(commission + HST)</small>'),
    }
    return {
        "verdict_word": f'{sc["verdict_word"]} comes out ahead',
        "gap_str": f'{sym}{abs(sc["gap"]):,.0f}',
        "by_str": f"net-worth gap{tax_tag} by year {years}",
        "stats": stats,
    }


@app.get("/", response_class=HTMLResponse)
def home(request: Request, _: None = Depends(require_auth)) -> str:
    return _page(FORM, _current_user(request))


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "accounts": _OAUTH_ON, "db": _DB_ON}


@app.get("/api/geocode")
def api_geocode(q: str = "") -> JSONResponse:
    """Resolve a typed address to a Canadian postal code + the region it routes to.

    Powers the form's "find by address" helper: the browser sends the address,
    we return the postal code and the regional label so the form can auto-fill
    the postal field. Returns ``{"ok": false}`` (200) when nothing is found, so
    the UI degrades to manual postal entry.
    """
    hit = geocode.geocode_address(q)
    if not hit:
        return JSONResponse({"ok": False})
    region = data.get_params(hit["postal"]).get("_region", "")
    return JSONResponse({
        "ok": True,
        "postal": hit["postal"],
        "city": hit.get("city", ""),
        "province": hit.get("province", ""),
        "region": region,
        "label": hit.get("label", ""),
    })


# --------------------------------------------------------------------------- #
# Methodology / transparency page
# --------------------------------------------------------------------------- #
def _linkify(text: str) -> str:
    """Escape text and turn bare http(s) URLs into links."""
    out = []
    for tok in str(text).split(" "):
        if tok.startswith("http"):
            url = html.escape(tok)
            out.append(f'<a href="{url}" target="_blank" rel="noopener">{url}</a>')
        else:
            out.append(html.escape(tok))
    return " ".join(out)


def _sources_html() -> str:
    blocks = ""
    for title, src in (("Market & regional data (evaluator/data.py)", data.SOURCES),
                       ("Tax, insurance & transaction costs (evaluator/tax.py)", tax.SOURCES)):
        items = "".join(
            f'<li><b>{html.escape(k.replace("_", " "))}:</b> {_linkify(v)}</li>'
            for k, v in src.items()
        )
        blocks += f"<h3>{html.escape(title)}</h3><ul>{items}</ul>"
    return blocks


def _methodology_html() -> str:
    return (
        PAGE_HEAD
        + '<section class="hero"><div class="hero-copy">'
        + '<span class="eyebrow"><span class="dot"></span> Transparency</span>'
        + '<h1>How this evaluator works</h1>'
        + '<p>The model, the assumptions behind every number, where the data comes '
        + 'from, and what it deliberately leaves out.</p>'
        + '</div></section>'
        + '<div class="prose">'

        + '<h2>What it does</h2>'
        + '<p>It compares two paths over your mortgage term: <b>buying</b> a home, versus '
        + '<b>renting and investing</b> the money you would have spent on ownership. Both '
        + 'people spend the same amount each month, so it is an apples-to-apples comparison. '
        + 'The headline is each path&rsquo;s projected <b>after-tax net worth</b> at the end '
        + 'of the term, and the year (if any) when one overtakes the other.</p>'

        + '<h2>How the comparison is kept fair</h2>'
        + '<ul>'
        + '<li><b>Matched cash flows.</b> The buyer pays mortgage P&amp;I, property tax, '
        + 'insurance, condo fees and maintenance. In the early years ownership usually costs '
        + 'more than rent, so the <i>renter</i> invests the difference. After the '
        + '&ldquo;crossover&rdquo; year &mdash; when rising rent passes the (largely fixed) '
        + 'ownership cost &mdash; the <i>owner</i> invests the difference instead.</li>'
        + '<li><b>Up-front cash.</b> The renter also invests what the buyer sank into the '
        + 'down payment and closing costs, so neither side gets a free head start.</li>'
        + '<li><b>One lump at the end.</b> Net worth = the owner&rsquo;s home equity (net of '
        + 'selling costs) plus their side investments, versus the renter&rsquo;s portfolio &mdash; '
        + 'each taxed appropriately (below).</li>'
        + '</ul>'

        + '<h2>The buying side</h2>'
        + '<ul>'
        + '<li><b>Mortgage:</b> fixed-rate, fully amortizing over the term (monthly P&amp;I).</li>'
        + '<li><b>Home value:</b> grows at a regional appreciation rate.</li>'
        + '<li><b>Carrying costs:</b> property tax (grows yearly), insurance, condo/HOA fees, '
        + 'and maintenance as a % of the home&rsquo;s value.</li>'
        + '<li><b>CMHC insurance</b> when the down payment is under 20% (premium financed into '
        + 'the loan; PST on it paid up front; homes over $1.5M can&rsquo;t be insured).</li>'
        + '</ul>'

        + '<h2>The renting + investing side</h2>'
        + '<p>The comparable rent is <b>estimated from your home&rsquo;s price within its region</b>, '
        + 'not a flat regional average: each region has a benchmark price/rent pair, and we scale '
        + 'the rent by your price using a sub-linear price-to-rent relationship (rent &asymp; '
        + 'price<sup>0.7</sup>), so a pricier home rents for more and a cheaper one for less &mdash; '
        + 'but gross rental yields fall as price rises, as they do in reality. That rent then grows '
        + 'each year. Whatever the renter saves versus owning is invested in a broad stock index at '
        + 'a long-run nominal return (dividends included). The owner does the same with any surplus '
        + 'after crossover. (Power users can still override the rent with the slider.)</p>'

        + '<h2>Tax layer</h2>'
        + '<ul>'
        + '<li><b>Registered accounts:</b> your age sets cumulative <b>TFSA</b> room; your '
        + 'income sets annual <b>RRSP</b> room and your marginal rate. The '
        + '&ldquo;shelter-first&rdquo; strategy fills TFSA, then RRSP, then a taxable account; '
        + 'RRSP refunds are reinvested and withdrawals taxed at a lower retirement rate.</li>'
        + '<li><b>Capital gains</b> on the taxable portion use the 50% inclusion rate.</li>'
        + '<li><b>Principal-residence exemption:</b> the home&rsquo;s own gain is tax-free on '
        + 'sale, so only the side investments are taxed.</li>'
        + '</ul>'

        + '<h2>Transaction costs</h2>'
        + '<p>At purchase: <b>province-correct land-transfer tax</b> &mdash; Ontario (plus '
        + 'Toronto&rsquo;s municipal LTT inside the city), BC&rsquo;s Property Transfer Tax, '
        + 'Manitoba&rsquo;s LTT, Qu&eacute;bec transfer duties (with Montr&eacute;al&rsquo;s luxury '
        + 'tiers), the Atlantic deed taxes, and the nominal land-title fees in Alberta and '
        + 'Saskatchewan (which levy no LTT) &mdash; plus legal/inspection fees, with first-time-buyer '
        + 'rebates where applicable. At sale: ~5% realtor commission + HST + legal. The buyer&rsquo;s '
        + 'up-front costs are credited to the renter&rsquo;s invested lump to keep things fair.</p>'

        + '<h2>Where the numbers come from</h2>'
        + '<p>Regional assumptions are matched to your postal code: researched values for '
        + 'Toronto/North York and 10 Ontario CMAs (Ottawa, Hamilton, Kitchener&ndash;Waterloo, '
        + 'London, Windsor, Oshawa, Barrie, Kingston, Guelph, St.&nbsp;Catharines&ndash;Niagara) '
        + 'and an Ontario-wide tier. <b>All ten provinces</b> route to a province-correct tier '
        + '(with metro tiers for Vancouver, Calgary, Edmonton, Montr&eacute;al, Winnipeg, and '
        + 'Halifax); the territories use a national fallback. The <b>5-year mortgage rate is live</b> '
        + 'from the Bank of Canada (cached, with an offline fallback). Anything regional can be '
        + 'overridden on the form. <em>Note: outside Ontario the land-transfer/property-tax rules and '
        + 'rates, benchmark prices, and rents are data-grounded (WOWA 2025 / CREA / CMHC); only the '
        + 'long-run appreciation is a forward-sustainable estimate.</em></p>'
        + _sources_html()

        + '<h2>Key assumptions</h2>'
        + '<ul>'
        + '<li>Rates (appreciation, rent growth, returns) are <b>long-run averages</b> applied '
        + 'smoothly &mdash; the real world is volatile and path-dependent.</li>'
        + '<li>By default one rate applies for the whole amortization; tick <b>&ldquo;Model '
        + '5-year renewals&rdquo;</b> to renew at a chosen rate every 5 years (Canadian style) &mdash; '
        + 'the payment resets on the remaining balance at each renewal.</li>'
        + '<li>Insurance and condo fees grow ~3%/yr (not held flat), like the property-tax bill.</li>'
        + '<li>Rent for a comparable home is a single figure, not a range.</li>'
        + '<li>Figures are in <b>future (nominal) dollars</b> by default; tick '
        + '<b>&ldquo;Show in today&rsquo;s dollars&rdquo;</b> in the what-if panel to deflate '
        + 'everything to present-day purchasing power (default 2%/yr inflation). It never changes '
        + 'the verdict &mdash; both sides are deflated by the same factor &mdash; it just makes the '
        + 'magnitudes intuitive.</li>'
        + '</ul>'

        + '<h2>What it deliberately leaves out</h2>'
        + '<ul>'
        + '<li><b>Horizon:</b> the comparison runs to the end of the mortgage term. A renter '
        + 'could of course buy later; net worth at the term end is a complete snapshot, so '
        + 'terminal value / imputed rent beyond the term is out of scope.</li>'
        + '<li>Volatility and sequence-of-returns risk (a single average return is used; the '
        + 'sensitivity heatmap shows how the verdict shifts across assumptions).</li>'
        + '<li>Lifestyle factors, mobility, maintenance effort, and non-financial value.</li>'
        + '<li>Per-city Teranet index reads for the non-Ontario provinces (appreciation there is a '
        + 'forward-sustainable estimate; the LTT, property-tax, benchmark, and rent figures are grounded).</li>'
        + '</ul>'
        + '<p>See <code>knowledge/METHODOLOGY_GAPS.md</code> in the repository for the detailed '
        + 'list of modelling gaps and decisions.</p>'

        + '</div>'
        + '<p style="text-align:center;margin:1.5rem 0"><a class="back" href="/">'
        + '&larr; Back to the evaluator</a></p>'
        + PAGE_FOOT
    )


@app.get("/methodology", response_class=HTMLResponse)
def methodology(request: Request, _: None = Depends(require_auth)):
    return HTMLResponse(_page(_methodology_html(), _current_user(request)))


# --------------------------------------------------------------------------- #
# Admin analytics dashboard (restricted to EVALUATOR_ADMIN_EMAILS)
# --------------------------------------------------------------------------- #
def _require_admin(request: Request) -> dict:
    user = _require_login(request)   # 404 if accounts off, 401 if not signed in
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admins only.")
    return user


def _bar_rows(pairs, total: int) -> str:
    """Render label / count rows with a proportional bar."""
    out = ""
    top = max((c for _, c in pairs), default=0) or 1
    for label, count in pairs:
        pct = (count / total * 100) if total else 0
        w = count / top * 100
        out += (
            '<div class="bar-row"><span class="bar-label">' + html.escape(str(label)) + '</span>'
            f'<span class="bar-track"><span class="bar-fill" style="width:{w:.0f}%"></span></span>'
            f'<span class="bar-num">{count} <small>({pct:.0f}%)</small></span></div>'
        )
    return out or '<p class="scen-meta">No data yet.</p>'


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request):
    user = _require_admin(request)
    a = analytics.summary()
    total = a["total"]

    def tile(label, value):
        return (f'<div class="stat"><span class="k">{label}</span>'
                f'<span class="v">{value}</span></div>')

    tiles = (
        '<div class="result-summary reveal">'
        + tile("Total runs", f'{total:,}')
        + tile("Last 7 days", f'{a["last_7"]:,}')
        + tile("Last 30 days", f'{a["last_30"]:,}')
        + tile("Buying wins", f'{a["buy"]:,}')
        + tile("Renting wins", f'{a["rent"]:,}')
        + tile("Signed-in", f'{a["signed_in"]:,}')
        + tile("Anonymous", f'{a["anonymous"]:,}')
        + '</div>'
    )

    sections = (
        '<div class="admin-grid">'
        + '<section class="admin-card"><h2>Verdict split</h2>'
        + _bar_rows([("Buying", a["buy"]), ("Renting", a["rent"])], a["buy"] + a["rent"]) + '</section>'
        + '<section class="admin-card"><h2>Sign-in</h2>'
        + _bar_rows([("Signed-in", a["signed_in"]), ("Anonymous", a["anonymous"])],
                    a["signed_in"] + a["anonymous"]) + '</section>'
        + '<section class="admin-card"><h2>Price range</h2>'
        + _bar_rows(a["price_buckets"], total) + '</section>'
        + '<section class="admin-card"><h2>Top postal areas (FSA)</h2>'
        + _bar_rows(a["top_fsa"], total) + '</section>'
        + '<section class="admin-card"><h2>Top regions</h2>'
        + _bar_rows(a["top_region"], total) + '</section>'
        + '</div>'
    )

    body = (
        PAGE_HEAD
        + '<section class="hero"><div class="hero-copy">'
        + '<span class="eyebrow"><span class="dot"></span> Admin</span>'
        + '<h1>Usage analytics</h1><p>Anonymized evaluation activity. No personal data '
        + 'is stored &mdash; only region, price range, and the verdict.</p></div></section>'
        + tiles + sections
        + '<p><a class="back" href="/">&larr; Back to the evaluator</a></p>'
        + PAGE_FOOT
    )
    return HTMLResponse(_page(body, user))


# --------------------------------------------------------------------------- #
# Google OAuth sign-in (only registered when accounts are enabled).
# --------------------------------------------------------------------------- #
@app.get("/login")
async def login(request: Request):
    if not _OAUTH_ON:
        raise HTTPException(status_code=404, detail="Accounts are not enabled.")
    redirect_uri = (os.environ.get("EVALUATOR_OAUTH_REDIRECT", "").strip()
                    or str(request.url_for("auth_google")))
    return await _oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/auth/google/callback", name="auth_google")
async def auth_google(request: Request):
    if not _OAUTH_ON:
        raise HTTPException(status_code=404, detail="Accounts are not enabled.")
    try:
        token = await _oauth.google.authorize_access_token(request)
    except OAuthError:
        return RedirectResponse(url="/?login=failed")
    info = token.get("userinfo") or {}
    sub = info.get("sub")
    if not sub:
        return RedirectResponse(url="/?login=failed")
    user = accounts.upsert_google_user(
        google_sub=sub, email=info.get("email", ""),
        name=info.get("name"), picture=info.get("picture"),
    )
    request.session["user_id"] = user["id"]
    return RedirectResponse(url="/")


@app.get("/logout")
def logout(request: Request):
    if _ACCOUNTS_IMPORTABLE:
        request.session.clear()
    return RedirectResponse(url="/")


# --------------------------------------------------------------------------- #
# Saved scenarios (require a signed-in user; 404 when accounts are disabled).
# --------------------------------------------------------------------------- #
class ScenarioIn(BaseModel):
    name: str
    inputs: dict
    id: int | None = None


def _require_login(request: Request) -> dict:
    """Return the signed-in user or raise (404 if accounts off, 401 if not signed in)."""
    if not _DB_ON:
        raise HTTPException(status_code=404, detail="Accounts are not enabled.")
    user = _current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to use saved scenarios.")
    return user


@app.post("/api/scenarios")
def save_scenario(payload: ScenarioIn, request: Request):
    user = _require_login(request)
    name = (payload.name or "").strip()[:120]
    if not name:
        raise HTTPException(status_code=400, detail="Please give the scenario a name.")
    try:
        inputs = _sanitize_inputs(payload.inputs)
        if "price" not in inputs:
            raise ValueError("missing price")
        sc = _run_from_inputs(inputs)          # validates inputs + builds snapshot
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Could not save: {exc}")
    snap = _snapshot(sc)
    if payload.id is not None:
        row = scenarios.update(payload.id, user["id"], name=name, inputs=inputs, snapshot=snap)
        if row is None:
            raise HTTPException(status_code=404, detail="Scenario not found.")
    else:
        row = scenarios.create(user["id"], name, inputs, snap)
    return {"ok": True, "id": row["id"], "name": row["name"]}


@app.get("/scenarios", response_class=HTMLResponse)
def scenarios_list(request: Request):
    user = _require_login(request)
    rows = scenarios.list_for_user(user["id"])
    if rows:
        cards = ""
        for r in rows:
            snap = r.get("snapshot") or {}
            verdict = html.escape(snap.get("verdict_word", "—"))
            gap = html.escape(snap.get("gap_str", ""))
            tax_tag = " after tax" if snap.get("after_tax") else ""
            region = html.escape(snap.get("region", ""))
            updated = html.escape((r.get("updated_at") or "")[:10])
            nm = html.escape(r["name"])
            cards += (
                '<div class="scen-card">'
                f'<input type="checkbox" class="scen-pick" value="{r["id"]}" '
                'onchange="cmpSync()" title="Select to compare">'
                '<div class="scen-main">'
                f'<div class="scen-name">{nm}</div>'
                f'<div class="scen-meta"><span class="scen-verdict">{verdict} ahead by {gap}'
                f'{tax_tag}</span> &middot; {region} &middot; saved {updated}</div>'
                '</div>'
                '<div class="scen-actions">'
                f'<a class="open" href="/scenarios/{r["id"]}/open">Open</a>'
                f'<form class="scen-form" method="post" '
                f'data-base="/scenarios/{r["id"]}/rename" data-name="{nm}" '
                'onsubmit="var n=prompt(\'Rename scenario:\',this.dataset.name);'
                'if(n===null||!n.trim())return false;'
                'this.action=this.dataset.base+\'?name=\'+encodeURIComponent(n.trim());return true;">'
                '<button type="submit">Rename</button></form>'
                f'<form class="scen-form" method="post" action="/scenarios/{r["id"]}/delete" '
                'onsubmit="return confirm(\'Delete this scenario?\');">'
                '<button class="del" type="submit">Delete</button></form>'
                '</div>'
                '</div>'
            )
        cmp_bar = ''
        if len(rows) >= 2:
            cmp_bar = (
                '<div class="cmp-bar"><span id="cmp-count">Select 2–4 scenarios to compare</span>'
                '<button id="cmp-btn" type="button" disabled onclick="cmpGo()">Compare selected</button>'
                '</div>'
            )
        body_inner = (
            cmp_bar + f'<div class="scen-list">{cards}</div>'
            + '<script>function cmpPicked(){return [].slice.call('
            + 'document.querySelectorAll(".scen-pick:checked")).map(function(c){return c.value;});}'
            + 'function cmpSync(){var n=cmpPicked().length;var b=document.getElementById("cmp-btn");'
            + 'if(!b)return;b.disabled=(n<2||n>4);'
            + 'document.getElementById("cmp-count").textContent=n?("Comparing "+n+" selected"):'
            + '"Select 2–4 scenarios to compare";}'
            + 'function cmpGo(){var ids=cmpPicked();if(ids.length>=2)'
            + 'location.href="/compare?ids="+ids.join(",");}</script>'
        )
    else:
        body_inner = ('<div class="scen-empty">No saved scenarios yet. Run an '
                      'evaluation, then use <b>Save scenario</b> on the results page.</div>')
    body = (
        PAGE_HEAD
        + '<section class="hero"><div class="hero-copy">'
        + '<span class="eyebrow"><span class="dot"></span> Your account</span>'
        + '<h1>Saved scenarios</h1>'
        + '<p>Reopen, rename, delete, or select a few to compare side by side.</p>'
        + '</div></section>'
        + body_inner
        + '<p><a class="back" href="/">&larr; New evaluation</a></p>'
        + PAGE_FOOT
    )
    return HTMLResponse(_page(body, user))


@app.get("/scenarios/{scenario_id}/open")
def scenario_open(scenario_id: int, request: Request):
    user = _require_login(request)
    row = scenarios.get(scenario_id, user["id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Scenario not found.")
    return RedirectResponse(url="/evaluate?" + _scenario_query(row["inputs"], scenario_id))


@app.post("/scenarios/{scenario_id}/rename")
def scenario_rename(scenario_id: int, request: Request, name: str = ""):
    user = _require_login(request)
    new_name = (name or "").strip()[:120]
    if new_name:
        scenarios.update(scenario_id, user["id"], name=new_name)
    return RedirectResponse(url="/scenarios", status_code=303)


@app.post("/scenarios/{scenario_id}/delete")
def scenario_delete(scenario_id: int, request: Request):
    user = _require_login(request)
    scenarios.delete(scenario_id, user["id"])
    return RedirectResponse(url="/scenarios", status_code=303)


# --------------------------------------------------------------------------- #
# Run history ("my runs")
# --------------------------------------------------------------------------- #
@app.get("/history", response_class=HTMLResponse)
def history_page(request: Request):
    user = _require_login(request)
    rows = history.list_for_user(user["id"])
    if rows:
        cards = ""
        for r in rows:
            snap = r.get("snapshot") or {}
            inp = r.get("inputs") or {}
            verdict = html.escape(snap.get("verdict_word", "—"))
            gap = html.escape(snap.get("gap_str", ""))
            tax_tag = " after tax" if snap.get("after_tax") else ""
            region = html.escape(snap.get("region", snap.get("postal", "")))
            when = html.escape((r.get("created_at") or "").replace("T", " ")[:16])
            q = html.escape(_scenario_query(inp))
            inp_json = html.escape(json.dumps(inp))
            cards += (
                '<div class="scen-card">'
                '<div class="scen-main">'
                f'<div class="scen-name">{region or "Evaluation"}</div>'
                f'<div class="scen-meta"><span class="scen-verdict">{verdict} ahead by {gap}'
                f'{tax_tag}</span> &middot; {when}</div>'
                '</div>'
                '<div class="scen-actions">'
                f'<a class="open" href="/evaluate?{q}">Open</a>'
                f'<button type="button" data-inputs="{inp_json}" '
                'onclick="histSave(this)">Save</button>'
                '</div>'
                '</div>'
            )
        inner = f'<div class="scen-list">{cards}</div>'
    else:
        inner = ('<div class="scen-empty">No runs yet. Each evaluation you run while '
                 'signed in is recorded here.</div>')
    body = (
        PAGE_HEAD
        + '<section class="hero"><div class="hero-copy">'
        + '<span class="eyebrow"><span class="dot"></span> Your account</span>'
        + '<h1>My runs</h1><p>Your recent evaluations. Reopen one, or save it as a '
        + 'named scenario.</p></div></section>'
        + inner
        + '<p><a class="back" href="/">&larr; New evaluation</a></p>'
        + '<script>function histSave(btn){var inp=JSON.parse(btn.dataset.inputs);'
        + 'var name=prompt("Save as scenario \\u2014 name:");if(name===null||!name.trim())return;'
        + 'btn.textContent="Saving\\u2026";'
        + 'fetch("/api/scenarios",{method:"POST",headers:{"Content-Type":"application/json"},'
        + 'body:JSON.stringify({name:name.trim(),inputs:inp})}).then(function(r){return r.json();})'
        + '.then(function(d){btn.textContent=d.ok?"Saved \\u2713":"Failed";})'
        + '.catch(function(){btn.textContent="Failed";});}</script>'
        + PAGE_FOOT
    )
    return HTMLResponse(_page(body, user))


# --------------------------------------------------------------------------- #
# Compare scenarios (2–4 saved scenarios side by side)
# --------------------------------------------------------------------------- #
@app.get("/compare", response_class=HTMLResponse)
def compare_page(request: Request, ids: str = ""):
    user = _require_login(request)
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()][:4]
    rows = [r for r in (scenarios.get(i, user["id"]) for i in id_list) if r]
    if len(rows) < 2:
        return _error_page("Pick 2–4 of your saved scenarios to compare.")

    runs = []
    for r in rows:
        try:
            runs.append((r, _run_from_inputs(r["inputs"])))
        except Exception:  # noqa: BLE001 - skip a scenario that won't run
            continue
    if len(runs) < 2:
        return _error_page("Could not run enough of those scenarios to compare.")

    # Comparison table: one column per scenario.
    metrics = [
        ("Region", lambda r, sc: html.escape(sc["params"]["region_label"])),
        ("Home price", lambda r, sc: f'{sc["sym"]}{sc["params"]["purchase_price"]:,.0f}'),
        ("Down payment", lambda r, sc: _result_fields(sc)["stats"]["down"]),
        ("Mortgage", lambda r, sc: _result_fields(sc)["stats"]["mortgage"]),
        ("Verdict", lambda r, sc: f'{("Buying" if sc["leader"]=="buyer" else "Renting")}'),
        ("Net-worth gap", lambda r, sc: _result_fields(sc)["gap_str"]),
        ("Crossover", lambda r, sc: _result_fields(sc)["stats"]["crossover"]),
        ("Buy closing costs", lambda r, sc: _result_fields(sc)["stats"]["buy_costs"]),
        ("Sell costs", lambda r, sc: _result_fields(sc)["stats"]["sell_costs"]),
    ]
    head = "".join(f'<th>{html.escape(r["name"])}</th>' for r, _ in runs)
    body_rows = ""
    for label, fn in metrics:
        cells = "".join(f"<td>{fn(r, sc)}</td>" for r, sc in runs)
        body_rows += f"<tr><th>{label}</th>{cells}</tr>"
    table = (f'<div class="cmp-wrap"><table class="cmp-table"><thead><tr><th></th>{head}'
             f'</tr></thead><tbody>{body_rows}</tbody></table></div>')

    cmp_data = [{
        "name": r["name"],
        "years": sc["chart_data"]["years"],
        "buyer": sc["chart_data"]["c5"]["buyer"],
        "renter": sc["chart_data"]["c5"]["renter"],
    } for r, sc in runs]

    body = (
        PAGE_HEAD
        + '<section class="hero"><div class="hero-copy">'
        + '<span class="eyebrow"><span class="dot"></span> Your account</span>'
        + '<h1>Compare scenarios</h1><p>Net-worth gap (buyer minus renter, after tax) '
        + 'over time — above zero means buying wins.</p></div></section>'
        + '<div id="cmp-chart" class="plot" style="height:420px"></div>'
        + table
        + '<p><a class="back" href="/scenarios">&larr; Back to scenarios</a></p>'
        + '<script src="/static/plotly.min.js"></script>'
        + '<script>var CMP=' + json.dumps(cmp_data) + ';\n' + COMPARE_SCRIPT_BODY + '</script>'
        + PAGE_FOOT
    )
    return HTMLResponse(_page(body, user))


# --------------------------------------------------------------------------- #
# Shareable result links (read-only, no account needed to view)
# --------------------------------------------------------------------------- #
class ShareIn(BaseModel):
    inputs: dict


@app.post("/api/share")
def create_share(payload: ShareIn, request: Request):
    if not _DB_ON:
        raise HTTPException(status_code=404, detail="Sharing is not enabled.")
    try:
        inputs = _sanitize_inputs(payload.inputs)
        if "price" not in inputs:
            raise ValueError("missing price")
        sc = _run_from_inputs(inputs)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Could not create link: {exc}")
    row = shares.create(inputs, _snapshot(sc))
    return {"ok": True, "slug": row["slug"], "url": f"/r/{row['slug']}"}


@app.get("/r/{slug}", response_class=HTMLResponse)
def shared_result(slug: str, request: Request):
    if not _DB_ON:
        raise HTTPException(status_code=404, detail="Sharing is not enabled.")
    row = shares.get_by_slug(slug)
    if row is None:
        return _error_page("This shared link wasn't found (it may have been removed).")
    try:
        sc = _run_from_inputs(row["inputs"])
    except Exception as exc:  # noqa: BLE001
        return _error_page(f"Could not load this shared scenario: {exc}")
    return HTMLResponse(_page(_render_result_html(sc, row["inputs"], readonly=True),
                              _current_user(request)))


def _render_result_html(sc: dict, inputs: dict, *, user: dict | None = None,
                        sid: int | None = None, readonly: bool = False) -> str:
    """Build the full results page. Shared by /evaluate and /r/<slug> (read-only)."""
    params = sc["params"]
    chart_meta = [
        ("House price trajectory",
         "Projected market value of the home across the term."),
        ("Cumulative cost of ownership",
         "Up-front cash and what each payment covers, stacked over time."),
        ("Renter's invested savings",
         "Renting and investing the monthly cost difference instead of buying."),
        ("Owner's invested savings",
         "Buying and investing any monthly surplus alongside the home."),
        ("Net worth: buy vs rent (after tax)",
         "Total projected wealth under each path, side by side."),
        ("Sensitivity: who wins",
         "How the result shifts across home-appreciation and investment-return assumptions."),
    ]
    cards = ""
    for i, (title, cap) in enumerate(chart_meta):
        cards += (
            '<figure class="chart-card reveal">'
            '<div class="chart-head">'
            f'<span class="chart-num">{i + 1}</span>'
            f'<h2 class="chart-title">{html.escape(title)}</h2>'
            '</div>'
            f'<p class="chart-cap">{html.escape(cap)}</p>'
            f'<div id="chart-{i + 1}" class="plot"></div>'
            '</figure>'
        )
    imgs = f'<div class="charts" id="charts">{cards}</div>'

    f = _result_fields(sc)
    s = f["stats"]
    sym = sc["sym"]
    strat_label = sc["strat_label"].replace("&", "&amp;")

    shared_note = (
        '<div class="shared-note">Shared scenario (read-only). '
        '<a href="/">Run your own evaluation &rarr;</a></div>'
    ) if readonly else ""

    banner = (
        '<section class="result-banner reveal">'
        f'<div class="rb-art" aria-hidden="true">{RESULT_BADGE_SVG}</div>'
        '<div class="rb-body">'
        '<span class="eyebrow"><span class="dot"></span> Your results</span>'
        f"<h1>{html.escape(params['postal_code'])} &middot; {html.escape(params['region_label'])}</h1>"
        '<p class="verdict">'
        f'<span class="lead" id="wf-verdict">{f["verdict_word"]}</span>'
        f'<span class="v-amount" id="wf-gap">{f["gap_str"]}</span>'
        f'<span class="by" id="wf-by">{f["by_str"]}</span>'
        '</p>'
        '</div>'
        '</section>'
    )

    stats = (
        '<div class="result-summary reveal">'
        f'<div class="stat"><span class="k">Home price</span>'
        f'<span class="v" id="wf-price">{sym}{params["purchase_price"]:,.0f}</span></div>'
        f'<div class="stat"><span class="k">Down payment</span>'
        f'<span class="v" id="wf-down">{s["down"]}</span></div>'
        f'<div class="stat"><span class="k">Mortgage</span>'
        f'<span class="v" id="wf-mortgage">{s["mortgage"]}</span></div>'
        f'<div class="stat"><span class="k">Crossover</span>'
        f'<span class="v" id="wf-crossover">{s["crossover"]}</span></div>'
        f'<div class="stat"><span class="k">Strategy</span>'
        f'<span class="v" style="font-size:.95rem">{strat_label}</span></div>'
        f'<div class="stat"><span class="k">Renter tax at sale</span>'
        f'<span class="v" id="wf-renter-tax">{s["renter_tax"]}</span></div>'
        f'<div class="stat"><span class="k">Buy closing costs</span>'
        f'<span class="v" id="wf-buy-costs">{s["buy_costs"]}</span></div>'
        f'<div class="stat"><span class="k">Sell costs at yr {sc["years"]}</span>'
        f'<span class="v" id="wf-sell-costs">{s["sell_costs"]}</span></div>'
        '</div>'
    )

    d_pct = round(sc["pct"])
    r_rate = round(params["mortgage_rate"] * 100, 1)
    r_appr = round(params["appreciation_rate"] * 100, 1)
    r_ret = round(params["investment_return_rate"] * 100, 1)
    r_rent = int(round(params["rent_monthly"]))
    # Slider ceiling adapts so the (price-derived) default always fits and leaves
    # headroom to drag higher; min 10k, else ~2x the default rounded up to 1k.
    rent_max = max(10000, ((r_rent * 2) // 1000 + 1) * 1000)
    whatif = (
        '<section class="whatif" id="whatif">'
        '<h2>What-if &middot; adjust the assumptions <span id="wf-status">updating&hellip;</span></h2>'
        '<p class="wf-sub">Drag a slider to update the charts and verdict live.</p>'
        '<div class="wf-grid">'
        f'<div class="wf-row"><label>Down payment <span class="wf-val" id="wf-down-val">{d_pct}%</span></label>'
        f'<input type="range" id="s-down" min="5" max="50" step="1" value="{d_pct}"></div>'
        f'<div class="wf-row"><label>Mortgage rate <span class="wf-val" id="wf-rate-val">{r_rate}%</span></label>'
        f'<input type="range" id="s-rate" min="1" max="8" step="0.1" value="{r_rate}"></div>'
        f'<div class="wf-row"><label>Home appreciation <span class="wf-val" id="wf-appr-val">{r_appr}%</span></label>'
        f'<input type="range" id="s-appr" min="0" max="10" step="0.5" value="{r_appr}"></div>'
        f'<div class="wf-row"><label>Investment return <span class="wf-val" id="wf-ret-val">{r_ret}%</span></label>'
        f'<input type="range" id="s-ret" min="0" max="15" step="0.5" value="{r_ret}"></div>'
        f'<div class="wf-row"><label>Monthly rent <span class="wf-val" id="wf-rent-val">${r_rent:,}</span></label>'
        f'<input type="range" id="s-rent" min="1000" max="{rent_max}" step="100" value="{r_rent}"></div>'
        '</div>'
        '<div class="wf-real">'
        f'<label><input type="checkbox" id="wf-real" {"checked" if sc.get("show_real") else ""}> '
        'Show in today’s dollars</label>'
        '<span class="wf-real-infl">at <input type="number" id="wf-infl" min="0" max="10" step="0.5" '
        f'value="{sc.get("real_inflation", projections.DEFAULT_INFLATION) * 100:g}">%/yr inflation</span>'
        '<span class="wf-dollars-note" id="wf-dollars-note"></span>'
        '</div>'
        '<div class="wf-real wf-renew">'
        f'<label><input type="checkbox" id="wf-renew" {"checked" if sc.get("renewals") else ""}> '
        'Model 5-year renewals</label>'
        '<span class="wf-real-infl">renew at <input type="number" id="wf-renew-rate" min="0" max="15" '
        f'step="0.1" value="{sc["params"].get("renewal_rate", 0.055) * 100:g}">%</span>'
        '<span class="wf-dollars-note" id="wf-renew-note"></span>'
        '</div>'
        '</section>'
    )

    base_js = json.dumps({
        "price": inputs["price"], "years": sc["years"], "postal": inputs.get("postal", "M2J 0E8"),
        "age": inputs.get("age", 35), "income": inputs.get("income", 120000.0),
        "strategy": inputs.get("strategy", "shelter-first"),
        "first_time": "true" if inputs.get("first_time") else "false",
    })

    # Save panel (signed-in) + share panel (DB on) — both hidden on read-only pages.
    savebox = sharebox = ""
    if user and not readonly:
        sid_name = ""
        if sid is not None:
            existing = scenarios.get(sid, user["id"])
            if existing:
                sid_name = existing["name"]
        btn_label = "Update scenario" if sid_name else "Save scenario"
        savebox = (
            '<section class="savebox">'
            '<h2>Save scenario</h2>'
            f'<input type="text" id="scen-name" maxlength="120" placeholder="Name this scenario" '
            f'value="{html.escape(sid_name)}">'
            f'<button id="save-btn" type="button" onclick="saveScenario()">{btn_label}</button>'
            '<span class="save-status" id="save-status"></span>'
            '</section>'
        )
    if _DB_ON and not readonly:
        sharebox = (
            '<section class="savebox sharebox">'
            '<h2>Share</h2>'
            '<button type="button" onclick="shareScenario()">Create link</button>'
            '<input type="text" id="share-link" class="share-link" readonly '
            'style="display:none" onclick="this.select()">'
            '<span class="save-status" id="share-status"></span>'
            '</section>'
        )

    result_script = ""
    if (user or _DB_ON) and not readonly:
        result_script = (
            "var SCEN_ID=" + (str(int(sid)) if sid is not None else "null") + ";\n"
            "var RESULT_INPUTS=" + json.dumps(inputs) + ";\n"
            + RESULT_SCRIPT_BODY + "\n"
        )

    script = (
        '<script src="/static/plotly.min.js"></script>'
        "<script>\n"
        "var CHART_DATA=" + json.dumps(sc["chart_data"]) + ";\n"
        "var BASE=" + base_js + ";\n"
        + CHARTS_SCRIPT_BODY + "\n"
        + WHATIF_SCRIPT_BODY + "\n"
        + result_script + "</script>"
    )

    return (
        PAGE_HEAD
        + shared_note
        + banner
        + savebox
        + sharebox
        + stats
        + whatif
        + imgs
        + '<p><a class="back" href="/">&larr; Run another scenario</a> '
        + '&middot; <a class="back" href="/methodology">How this works</a></p>'
        + script
        + PAGE_FOOT
    )


def _inputs_from_args(price, down, years, postal, age, income, strategy, first_time,
                      rate, appreciation, rent, rent_growth, property_tax_rate,
                      investment_return, insurance, hoa, retirement_rate) -> dict:
    """Collapse evaluate's args into a compact inputs dict (drop defaults)."""
    inputs: dict = {
        "price": price, "down": down, "years": years, "postal": postal,
        "age": age, "income": income, "strategy": strategy, "first_time": first_time,
    }
    for k, v in (("rate", rate), ("appreciation", appreciation), ("rent", rent),
                 ("rent_growth", rent_growth), ("property_tax_rate", property_tax_rate),
                 ("investment_return", investment_return), ("retirement_rate", retirement_rate)):
        if v is not None:
            inputs[k] = v
    if insurance != 1500.0:
        inputs["insurance"] = insurance
    if hoa != 0.0:
        inputs["hoa"] = hoa
    return inputs


@app.get("/evaluate", response_class=HTMLResponse)
def evaluate(
    request: Request,
    price: float,
    down: str = "20%",
    years: int = 30,
    postal: str = "M2J 0E8",
    # Tax layer: registered-account sheltering + capital-gains tax.
    age: int = 35,
    income: float = 120000.0,
    strategy: str = "shelter-first",
    first_time: bool = False,
    # Optional power-user overrides (default to regional data when omitted).
    rate: float | None = None,
    appreciation: float | None = None,
    rent: float | None = None,
    rent_growth: float | None = None,
    property_tax_rate: float | None = None,
    investment_return: float | None = None,
    insurance: float = 1500.0,
    hoa: float = 0.0,
    retirement_rate: float | None = None,
    real: bool = False,             # show all dollars in today's (inflation-adjusted) money
    inflation: float | None = None,  # inflation for the real view (default 2%)
    renewals: bool = False,          # model 5-year rate renewals
    renewal_rate: float | None = None,
    sid: int | None = None,   # set when reopening a saved scenario (for "Update")
    _: None = Depends(require_auth),
):
    try:
        sc = _run_scenario(
            price=price, down=down, years=years, postal=postal,
            age=age, income=income, strategy=strategy, first_time=first_time,
            rate=rate, appreciation=appreciation, rent=rent, rent_growth=rent_growth,
            property_tax_rate=property_tax_rate, investment_return=investment_return,
            insurance=insurance, hoa=hoa, retirement_rate=retirement_rate,
            show_real=real, inflation=inflation, renewals=renewals, renewal_rate=renewal_rate,
        )
    except ValueError as exc:
        return _error_page(str(exc))

    inputs = _inputs_from_args(price, down, years, postal, age, income, strategy,
                               first_time, rate, appreciation, rent, rent_growth,
                               property_tax_rate, investment_return, insurance, hoa,
                               retirement_rate)
    user = _current_user(request)
    if user:  # auto-save to run history (deduped + pruned)
        try:
            history.record_run(user["id"], _sanitize_inputs(inputs), _snapshot(sc))
        except Exception:  # noqa: BLE001 - history is best-effort
            pass
    if _DB_ON:  # anonymized usage analytics (no personal data)
        try:
            analytics.record(
                fsa=data._fsa(postal), region=sc["params"]["region_label"],
                price=float(price), down_pct=round(sc["pct"], 1), years=int(sc["years"]),
                verdict=sc["leader"], gap=abs(sc["gap"]), after_tax=bool(sc["after_tax"]),
                signed_in=bool(user),
            )
        except Exception:  # noqa: BLE001 - analytics is best-effort
            pass
    return HTMLResponse(_page(_render_result_html(sc, inputs, user=user, sid=sid), user))


@app.get("/api/recompute")
def recompute(
    price: float,
    down: str = "20%",
    years: int = 30,
    postal: str = "M2J 0E8",
    age: int = 35,
    income: float = 120000.0,
    strategy: str = "shelter-first",
    first_time: bool = False,
    rate: float | None = None,
    appreciation: float | None = None,
    rent: float | None = None,
    investment_return: float | None = None,
    real: bool = False,
    inflation: float | None = None,
    renewals: bool = False,
    renewal_rate: float | None = None,
    _: None = Depends(require_auth),
) -> JSONResponse:
    """JSON endpoint powering the what-if sliders: new chart data + headline.

    Returns the chart series (for Plotly to re-render client-side) plus the
    dynamic display strings, so the results page updates in place without a reload.
    """
    try:
        sc = _run_scenario(
            price=price, down=down, years=years, postal=postal,
            age=age, income=income, strategy=strategy, first_time=first_time,
            rate=rate, appreciation=appreciation, rent=rent,
            investment_return=investment_return, show_real=real, inflation=inflation,
            renewals=renewals, renewal_rate=renewal_rate,
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    f = _result_fields(sc)
    return JSONResponse({
        "ok": True,
        "chart_data": sc["chart_data"],
        "verdict_word": f["verdict_word"],
        "gap_str": f["gap_str"],
        "by_str": f["by_str"],
        "stats": f["stats"],
    })
