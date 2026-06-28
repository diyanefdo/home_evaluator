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

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from evaluator import cli, projections, tax

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
 input:not([type=range]),select{width:100%;padding:.7rem .8rem;border:1px solid #cfd9e4;
   border-radius:10px;font-size:16px;background:#fbfdff;color:var(--ink);
   transition:border-color .15s,box-shadow .15s,background .15s}
 input:not([type=range]):hover,select:hover{border-color:#aebfd2}
 input:not([type=range]):focus,select:focus{outline:none;border-color:var(--brand);
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
</style></head><body>"""

PAGE_FOOT = "</body></html>"

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
function wfRecompute(){
  var q=new URLSearchParams(BASE);
  q.set('down', document.getElementById('s-down').value+'%');
  q.set('rate', (document.getElementById('s-rate').value/100));
  q.set('appreciation', (document.getElementById('s-appr').value/100));
  q.set('investment_return', (document.getElementById('s-ret').value/100));
  q.set('rent', document.getElementById('s-rent').value);
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
wfLabels();
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
    retirement_rate=None,
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
    )
    try:
        params = cli.build_engine_params(args)
        projection = projections.build_projection(params)
        projection["sensitivity"] = projections.build_sensitivity(params)
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
    stats = {
        "down": f'{sym}{params["down_payment"]:,.0f} <small>({sc["pct"]:.0f}%)</small>',
        "mortgage": f'{years} yr <small>@ {params["mortgage_rate"] * 100:.2f}%</small>{rate_badge}',
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
    _: None = Depends(require_auth),
):
    try:
        sc = _run_scenario(
            price=price, down=down, years=years, postal=postal,
            age=age, income=income, strategy=strategy, first_time=first_time,
            rate=rate, appreciation=appreciation, rent=rent, rent_growth=rent_growth,
            property_tax_rate=property_tax_rate, investment_return=investment_return,
            insurance=insurance, hoa=hoa, retirement_rate=retirement_rate,
        )
    except ValueError as exc:
        return _error_page(str(exc))
    params = sc["params"]

    # Title + caption for each chart, in render order. The browser draws these
    # interactively with Plotly into the chart-N divs below.
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

    # What-if sliders, initialized to this scenario's current assumptions.
    d_pct = round(sc["pct"])
    r_rate = round(params["mortgage_rate"] * 100, 1)
    r_appr = round(params["appreciation_rate"] * 100, 1)
    r_ret = round(params["investment_return_rate"] * 100, 1)
    r_rent = int(round(params["rent_monthly"]))
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
        f'<input type="range" id="s-rent" min="1000" max="10000" step="100" value="{r_rent}"></div>'
        '</div>'
        '</section>'
    )

    base_js = json.dumps({
        "price": price, "years": sc["years"], "postal": postal, "age": age,
        "income": income, "strategy": strategy,
        "first_time": "true" if first_time else "false",
    })
    script = (
        '<script src="/static/plotly.min.js"></script>'
        "<script>\n"
        "var CHART_DATA=" + json.dumps(sc["chart_data"]) + ";\n"
        "var BASE=" + base_js + ";\n"
        + CHARTS_SCRIPT_BODY + "\n"
        + WHATIF_SCRIPT_BODY + "\n</script>"
    )

    body = (
        PAGE_HEAD
        + banner
        + stats
        + whatif
        + imgs
        + '<p><a class="back" href="/">&larr; Run another scenario</a></p>'
        + '<p class="disclaimer">Projections use long-run historical assumptions '
        + 'and are not financial advice.</p>'
        + script
        + PAGE_FOOT
    )
    return HTMLResponse(body)


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
            investment_return=investment_return,
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
