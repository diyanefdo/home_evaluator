# Roadmap & Feature Ideas — Home Evaluator

A living backlog of improvements and new features, captured 2026-06-26. Grouped
by theme, each with a rough **effort** (S = hours, M = a day or two, L = a week+)
and **impact**, plus dependencies and risks. Nothing here is committed work —
it's a menu to prioritize from.

## Where the app is today (baseline)

- **Core:** a Python `evaluator` package (`data` → `projections` → `charts`) +
  a FastAPI `webapp.py`, packaged with Docker, reachable via Tailscale.
- **Modeling:** after-tax buy-vs-rent with registered accounts (TFSA/RRSP),
  capital-gains tax, principal-residence exemption, and transaction costs
  (land-transfer tax + sale commission).
- **Data:** hard-coded regional assumptions for Toronto/North York FSAs +
  national fallbacks (no live data).
- **State:** completely **stateless** — no database, no user accounts, every run
  is one-shot. Auth is optional HTTP basic-auth.
- **Known modeling gaps still open** (see `METHODOLOGY_GAPS.md`): #5 terminal
  value / imputed rent, #6 sensitivity, #7 minor polish.

---

## Theme 1 — Users, accounts & saved scenarios

> The biggest structural change: move from stateless to stateful. Everything
> else in this theme depends on a persistence layer.

| Idea | What | Effort | Impact |
|------|------|--------|--------|
| **Persistence layer** | Add a database. Start with **SQLite** (one file, zero-ops, fits the single-PC Docker setup; mount as a volume so it survives rebuilds). Migrate to Postgres only if multi-user/scale demands it. Use SQLModel/SQLAlchemy. | M | Foundational |
| **User accounts** | Replace basic-auth with real accounts (email + password, or magic-link, or OAuth via Google — the deploy already has a Google identity). Sessions via signed cookies. | M | High |
| **Saved scenarios** | Logged-in users can name and save a scenario (all inputs + a snapshot of the result). List, reopen, edit, delete. | M | High |
| **Scenario history / "my runs"** | Auto-save every evaluation to the user's history with a timestamp; let them revisit or re-run. | S–M | Medium |
| **Compare scenarios** | Pick 2–4 saved scenarios and render them side-by-side (e.g. buy-now vs wait-2-years, 20% vs 35% down, two neighbourhoods). | M | High |
| **Shareable result links** | Persist a result under a short slug (`/r/ab12cd`) so a scenario can be shared read-only without an account. | S | Medium |
| **Usage tracking / analytics** | Record per-run metadata (inputs, region, timestamp, anonymized user) to a table; build a small admin dashboard (most-queried postal codes, price ranges, buy-vs-rent verdict distribution). | M | Medium (product insight) |

**Dependencies:** persistence layer first, then accounts, then the rest.
**Risks/notes:** storing user financials = **PII**. Encrypt at rest, document a
privacy policy, add data-export/delete (PIPEDA-friendly). Don't store raw
passwords (use `argon2`/`bcrypt`). Keep the DB volume backed up.

---

## Theme 2 — Real estate & live market data

> Turn the tool from "type in assumptions" into "give me an address / area and
> I'll fill in the numbers." This is the highest-wow theme and a natural fit for
> the existing `canada-housing-financial-scraper` agent.

| Idea | What | Effort | Impact |
|------|------|--------|--------|
| **Nearby homes for sale** | Given a postal code / address, list comparable active listings (price, beds, sqft, link). Auto-suggest a realistic purchase price. | L | High |
| **Comparable rentals** | Pull nearby rental listings to ground the `rent_monthly` assumption (today it's a single hard-coded number — gap #7). Show a rent range, not one figure. | M–L | High |
| **Address autofill** | Enter an address → geocode → derive FSA, property-tax rate, and a price estimate; pre-fill the form. | M | High |
| **Live mortgage rates** | Fetch current 5-yr fixed/variable rates (e.g. Ratehub) instead of the baked-in 4.4%. | S–M | Medium |
| **Neighbourhood stats** | Price trends, days-on-market, price-to-rent ratio, school/transit scores for the area. | M | Medium |
| **More regions** | Expand `data.py` beyond North York: more Toronto FSAs, other cities/provinces, each with researched appreciation/rent/tax + correct land-transfer rules (BC PTT, no LTT in AB/SK, etc.). | M (ongoing) | High |

**Data sources & the big caveat:** there is **no free official MLS API**.
Options, roughly in order of legitimacy:
- **CREA DDF / RealtorLink** (requires brokerage membership) — the legit MLS feed.
- **Listing portals** (Realtor.ca, HouseSigma, Zillow) — scraping likely
  **violates ToS** and is fragile; risky for anything public.
- **Government / open data** — StatCan, CMHC HMIP (rents), municipal open-data
  (assessments, tax rates), Teranet–National Bank HPI (appreciation). Free and
  legit; great for trends, weaker for individual listings.
- **Paid APIs** — e.g. rental data providers, property-data aggregators.

**Recommendation:** start with the legit, free *aggregate* sources (CMHC rents,
StatCan/Teranet trends, municipal tax tables) to improve the *assumptions*; treat
live *individual listings* as a later, carefully-sourced feature. Cache
aggressively and attribute sources.

---

## Theme 3 — Modeling depth & accuracy

> Close the remaining `METHODOLOGY_GAPS.md` items and add realism. These make the
> output more trustworthy without changing the product shape.

| Idea | What | Effort | Impact |
|------|------|--------|--------|
| ~~**Sensitivity grid** (gap #6)~~ ✅ **DONE** | Chart 6: appreciation × investment-return heatmap with the scenario's cell outlined (`build_sensitivity()` + `_chart_sensitivity`). | M | High |
| **Terminal value / imputed rent** (gap #5) | Value the owner's near-rent-free years after payoff — extend a few years past the term or add a housing-cost differential. | M | Medium |
| **Monte Carlo** | Instead of point estimates, simulate distributions of returns/appreciation → "buying wins in X% of scenarios" with confidence bands. | L | High (credibility) |
| **Variable mortgage rates / renewals** | Model 5-yr renewals at projected rates instead of one fixed rate for 30 years (more realistic for Canada). | M | Medium |
| **CMHC insurance for <20% down** | Add mortgage default insurance premiums when down payment is under 20%. | S | Medium |
| **Real (inflation-adjusted) view** (gap #7) | Toggle nominal vs today's-dollars. | S | Medium |
| **Rental income / house hacking** | Model a basement suite or roommate offsetting ownership cost. | M | Medium |
| ~~**Sliders / what-if**~~ ✅ **DONE** | Results page has live sliders (down %, rate, appreciation, return, rent); debounced `/api/recompute` re-renders all 6 charts + the headline verdict in place. | M | High (engagement) |

---

## Theme 4 — UX, output & sharing

| Idea | What | Effort | Impact |
|------|------|--------|--------|
| ~~**Interactive charts**~~ ✅ **DONE** | Web charts are client-side **Plotly** (vendored locally at `static/plotly.min.js`, no CDN) — hover tooltips, clickable legend, zoom/reset. Server sends chart *data* (not PNGs); CLI still uses matplotlib. | M–L | High |
| **PDF / email report** | "Download as PDF" or email a formatted summary the user can keep or share with a partner/advisor. | M | Medium |
| **PWA / installable** | Make the web app installable on phones (offline shell, home-screen icon). | S–M | Medium |
| **Presets & examples** | One-click sample scenarios ("first condo", "detached + suite") to onboard new users. | S | Low–Med |
| **Bilingual (EN/FR)** | French support — relevant for a Canadian tool. | M | Medium |
| **Dark mode** | Theme toggle. | S | Low |

---

## Theme 5 — Engineering, trust & ops

| Idea | What | Effort | Impact |
|------|------|--------|--------|
| **Automated tests + CI** | Unit tests for the engine (amortization, tax, LTT) and a GitHub Actions workflow. The math is the product — it needs a safety net. | M | High |
| **Rate limiting** | Protect the public endpoint from abuse (per-IP limits). | S | Medium |
| **Real secrets management** | Move beyond `.env`; rotate credentials; never log PII. | S | Medium |
| **Observability** | Structured logging, error tracking (Sentry), basic uptime/health metrics. | S–M | Medium |
| **Methodology/transparency page** | An in-app page explaining every assumption with sources and the disclaimer — builds trust and reduces "is this legit?" friction. | S | Medium |
| **Stronger disclaimers** | Clear "not financial advice" + assumptions surfaced near the result, not just in a footnote. | S | Medium (legal) |
| **DB backups** | If/when stateful, automate backups of the SQLite/Postgres volume. | S | Medium |

---

## Suggested phasing

A pragmatic order that front-loads value and respects dependencies:

**Phase 1 — Finish the model + de-risk (low effort, high trust)**
- ✅ Sensitivity grid (#6) — done (Chart 6). Terminal value (#5) still to do.
- Live mortgage rates + CMHC insurance for <20% down.
- Automated tests + CI for the engine.
- Methodology/transparency page + clearer disclaimers.

**Phase 2 — Go stateful (the platform shift)**
- SQLite persistence (volume-mounted) + real accounts (Google OAuth).
- Saved scenarios, run history, shareable links.
- Basic usage analytics / admin dashboard.

**Phase 3 — Live market data (the wow factor)**
- Improve assumptions from free aggregate sources (CMHC rents, Teranet/StatCan
  trends, municipal tax tables) + more regions.
- Address autofill + comparable rentals.
- Nearby listings (only with a properly-sourced/legal data feed).

**Phase 4 — Polish & engagement**
- ✅ Interactive charts (Plotly) + what-if sliders — done.
- PDF/email reports, PWA, bilingual.
- Monte Carlo simulation.

---

## Cross-cutting concerns (apply to everything above)

- **Privacy/PII:** the moment we store incomes, savings, and addresses we hold
  sensitive data — encryption, retention limits, export/delete, a privacy policy.
- **Legal/ToS:** scraping listing portals is risky; prefer official/open data and
  attribute it. Keep the "not financial advice" disclaimer prominent.
- **Data freshness:** assumptions go stale — show a "data vintage" date and a way
  to refresh sources (the scraper agent can own this).
- **Keep the engine pure:** the `evaluator` package should stay UI- and
  storage-agnostic so the CLI, web, future mobile, and any API all reuse it.
