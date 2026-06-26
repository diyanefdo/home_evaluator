"""FastAPI web layer for the Canadian buy-vs-rent home evaluator.

Wraps the existing ``evaluator`` package: takes the four inputs from a form (or
query string), runs the projection + charting pipeline, and returns a single
HTML page with all five charts embedded inline (base64 PNGs — no file serving).

Run locally:
    uvicorn webapp:app --host 0.0.0.0 --port 8000
    # then open http://localhost:8000

See knowledge/DOCKER_PRIVATE_DEPLOYMENT.md for containerized + private-link use.
"""

from __future__ import annotations

import argparse
import base64
import html
import os
import secrets
import shutil
import tempfile

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from evaluator import cli, projections, charts

app = FastAPI(title="Canadian Buy-vs-Rent Home Evaluator")

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
 input,select{width:100%;padding:.7rem .8rem;border:1px solid #cfd9e4;border-radius:10px;
   font-size:16px;background:#fbfdff;color:var(--ink);
   transition:border-color .15s,box-shadow .15s,background .15s}
 input:hover,select:hover{border-color:#aebfd2}
 input:focus,select:focus{outline:none;border-color:var(--brand);
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
 .chart-card img{margin:0;border:1px solid var(--line);border-radius:10px;box-shadow:none}
 .chart-img{cursor:zoom-in}
 .chart-hint{display:block;margin-top:.5rem;text-align:right;color:var(--muted);
   font-size:.78rem;font-weight:500;letter-spacing:.01em}

 /* fullscreen tap-to-expand chart viewer */
 .lightbox{position:fixed;inset:0;z-index:1000;display:none;
   align-items:flex-start;justify-content:center;padding:1.2rem;
   background:rgba(8,20,32,.93);overflow:auto;-webkit-overflow-scrolling:touch}
 .lightbox.open{display:flex}
 .lightbox img{width:auto;height:auto;max-width:100%;max-height:92vh;margin:auto;
   background:#fff;border-radius:10px;box-shadow:0 18px 50px rgba(0,0,0,.55);
   cursor:zoom-in}
 .lightbox img.zoomed{max-width:none;max-height:none;width:1400px;cursor:zoom-out}
 .lb-close{position:fixed;top:.6rem;right:.8rem;z-index:1001;width:44px;height:44px;
   border:0;border-radius:50%;background:rgba(255,255,255,.16);color:#fff;
   font-size:1.7rem;line-height:1;cursor:pointer;
   transition:background .15s}
 .lb-close:hover{background:rgba(255,255,255,.28)}

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
</style></head><body>"""

PAGE_FOOT = "</body></html>"

# Fullscreen tap-to-expand viewer for charts (improves legibility on phones).
# A single overlay is reused; openLb() points it at the tapped chart's source so
# the (large) base64 image isn't duplicated in the DOM. Tap the image to toggle
# zoom-to-actual-size (then pan by scrolling / pinch); tap the backdrop, the
# close button, or press Esc to dismiss.
LIGHTBOX = """
<div id="lightbox" class="lightbox" onclick="closeLb(event)">
  <button type="button" class="lb-close" aria-label="Close" onclick="closeLb(event,true)">&times;</button>
  <img id="lb-img" alt="Expanded chart" onclick="toggleZoom(event)">
</div>
<script>
function openLb(el){var lb=document.getElementById('lightbox'),im=document.getElementById('lb-img');
 im.src=el.src;im.alt=el.alt||'Expanded chart';im.classList.remove('zoomed');
 lb.classList.add('open');document.body.style.overflow='hidden';}
function closeLb(e,force){if(force||(e&&e.target&&e.target.id==='lightbox')){
 document.getElementById('lightbox').classList.remove('open');document.body.style.overflow='';}}
function toggleZoom(e){e.stopPropagation();e.currentTarget.classList.toggle('zoomed');}
document.addEventListener('keydown',function(e){if(e.key==='Escape')closeLb(null,true);});
</script>
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
       five clear charts from one scenario.</p>
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
  <p class="lead">Enter the four details below to generate your comparison charts.</p>
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
        <label for="postal">Postal code</label>
        <input id="postal" name="postal" value="M2J 0E8">
        <small>Sets regional rates &amp; rent.</small>
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
    </div>
    <button type="submit">Evaluate scenario</button>
  </form>
</div>

<p class="disclaimer">
  Projections use long-run historical assumptions and are not financial advice.
</p>
""" + PAGE_FOOT


def _error_page(message: str) -> HTMLResponse:
    body = (
        PAGE_HEAD
        + f'<h1>Home Evaluator</h1><div class="err">{html.escape(message)}</div>'
        + '<p><a class="back" href="/">&larr; Back to the form</a></p>'
        + PAGE_FOOT
    )
    return HTMLResponse(body, status_code=400)


@app.get("/", response_class=HTMLResponse)
def home(_: None = Depends(require_auth)) -> str:
    return FORM


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/evaluate", response_class=HTMLResponse)
def evaluate(
    price: float,
    down: str = "20%",
    years: int = 30,
    postal: str = "M2J 0E8",
    # Tax layer: registered-account sheltering + capital-gains tax.
    age: int = 35,
    income: float = 120000.0,
    strategy: str = "shelter-first",
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
    _: None = Depends(require_auth),
):
    # --- validate inputs up front (don't rely on the CLI's SystemExit) -------
    if not (0 < price <= MAX_PRICE):
        return _error_page(f"Price must be between $1 and ${MAX_PRICE:,.0f}.")
    if not (1 <= years <= MAX_YEARS):
        return _error_page(f"Mortgage term must be between 1 and {MAX_YEARS} years.")
    try:
        down_amount = cli._parse_down_payment(down, price)
    except ValueError:
        return _error_page(f"Could not read down payment '{down}'. Use e.g. 200000 or 20%.")
    if not (0 < down_amount < price):
        return _error_page("Down payment must be greater than $0 and less than the price.")
    if not (18 <= age <= 100):
        return _error_page("Age must be between 18 and 100.")
    if not (0 <= income <= 100_000_000):
        return _error_page("Annual income must be $0 or more.")
    if strategy not in ("shelter-first", "taxable-only"):
        return _error_page("Investing strategy must be 'shelter-first' or 'taxable-only'.")

    # --- run the pipeline (reuse the CLI's validated param mapping) ----------
    args = argparse.Namespace(
        price=price, down=down, years=years, postal=postal,
        age=age, income=income, account_strategy=strategy, retirement_rate=retirement_rate,
        rate=rate, appreciation=appreciation, rent=rent, rent_growth=rent_growth,
        property_tax_rate=property_tax_rate, investment_return=investment_return,
        insurance=insurance, hoa=hoa, out=None, no_charts=False,
    )
    try:
        params = cli.build_engine_params(args)
        projection = projections.build_projection(params)
        summary = projections.compute_summary(projection, params)
    except Exception as exc:  # noqa: BLE001 - surface any modelling error to the user
        return _error_page(f"Could not evaluate this scenario: {exc}")

    # Title + caption for each chart, in the order generate_charts() emits them.
    chart_meta = [
        ("House price trajectory",
         "Projected market value of the home across the term."),
        ("Down payment and monthly costs",
         "Up-front cash and what each monthly payment covers."),
        ("Renter's invested savings",
         "Renting and investing the monthly cost difference instead of buying."),
        ("Owner's invested savings",
         "Buying and investing any monthly surplus alongside the home."),
        ("Net worth: buy vs rent",
         "Total projected wealth under each path, side by side."),
    ]

    out_dir = tempfile.mkdtemp(prefix="charts_")
    try:
        paths = charts.generate_charts(projection, params, out_dir)
        cards = ""
        for i, p in enumerate(paths):
            with open(p, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode()
            title, cap = chart_meta[i] if i < len(chart_meta) else (f"Chart {i + 1}", "")
            cards += (
                '<figure class="chart-card reveal">'
                '<div class="chart-head">'
                f'<span class="chart-num">{i + 1}</span>'
                f'<h2 class="chart-title">{html.escape(title)}</h2>'
                '</div>'
                f'<p class="chart-cap">{html.escape(cap)}</p>'
                f'<img class="chart-img" src="data:image/png;base64,{b64}"'
                f' alt="{html.escape(title)}" onclick="openLb(this)">'
                '<span class="chart-hint">&#9974; Tap to enlarge</span>'
                '</figure>'
            )
        imgs = f'<div class="charts">{cards}</div>'
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)

    sym = params["currency_symbol"]
    cy = summary.get("crossover_year")
    cross = f"~ year {cy:.1f}" if cy else "Not within term"
    gap = summary["final_buyer_minus_renter"]
    leader = "buyer" if gap >= 0 else "renter"
    verdict_word = "Buying" if leader == "buyer" else "Renting"
    pct = params["down_payment"] / params["purchase_price"] * 100
    after_tax = summary.get("after_tax")
    tax_tag = " after tax" if after_tax else ""
    strat_label = ("TFSA &amp; RRSP first" if summary.get("account_strategy") == "shelter-first"
                   else "Taxable only")

    banner = (
        '<section class="result-banner reveal">'
        f'<div class="rb-art" aria-hidden="true">{RESULT_BADGE_SVG}</div>'
        '<div class="rb-body">'
        '<span class="eyebrow"><span class="dot"></span> Your results</span>'
        f"<h1>{html.escape(params['postal_code'])} &middot; {html.escape(params['region_label'])}</h1>"
        '<p class="verdict">'
        f'<span class="lead">{verdict_word} comes out ahead</span>'
        f'<span class="v-amount">{sym}{abs(gap):,.0f}</span>'
        f'<span class="by">net-worth gap{tax_tag} by year {years}</span>'
        '</p>'
        '</div>'
        '</section>'
    )

    # Optional after-tax stat tiles (only when the tax layer ran).
    tax_tiles = ""
    if after_tax:
        tax_tiles = (
            f'<div class="stat"><span class="k">Strategy</span>'
            f'<span class="v" style="font-size:.95rem">{strat_label}</span></div>'
            f'<div class="stat"><span class="k">Renter tax at sale</span>'
            f'<span class="v">{sym}{summary.get("renter_tax_paid", 0):,.0f}</span></div>'
            f'<div class="stat"><span class="k">Buyer tax at sale</span>'
            f'<span class="v">{sym}{summary.get("buyer_tax_paid", 0):,.0f} <small>(home exempt)</small></span></div>'
        )

    stats = (
        '<div class="result-summary reveal">'
        f'<div class="stat"><span class="k">Home price</span>'
        f'<span class="v">{sym}{params["purchase_price"]:,.0f}</span></div>'
        f'<div class="stat"><span class="k">Down payment</span>'
        f'<span class="v">{sym}{params["down_payment"]:,.0f} <small>({pct:.0f}%)</small></span></div>'
        f'<div class="stat"><span class="k">Mortgage</span>'
        f'<span class="v">{years} yr <small>@ {params["mortgage_rate"] * 100:.2f}%</small></span></div>'
        f'<div class="stat"><span class="k">Crossover</span>'
        f'<span class="v">{cross}</span></div>'
        + tax_tiles
        + '</div>'
    )

    body = (
        PAGE_HEAD
        + banner
        + stats
        + imgs
        + '<p><a class="back" href="/">&larr; Run another scenario</a></p>'
        + '<p class="disclaimer">Projections use long-run historical assumptions '
        + 'and are not financial advice.</p>'
        + LIGHTBOX
        + PAGE_FOOT
    )
    return HTMLResponse(body)
