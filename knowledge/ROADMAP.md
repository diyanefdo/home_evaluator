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
- **Data:** regional assumptions for Toronto/North York, **10 researched Ontario
  CMAs** (Ottawa, Hamilton, KW, London, Windsor, Oshawa, Barrie, Kingston,
  Guelph, St. Catharines–Niagara, routed by FSA prefix), an Ontario provincial
  default, and national fallbacks. The **5-year mortgage rate is live** (Bank of
  Canada Valet API, cached, with offline fallback); other inputs are researched
  constants.
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
| **Persistence layer** ✅ | **DONE.** PostgreSQL in its own container (`db` service, named volume `evaluator-pgdata`, `depends_on: service_healthy`). App connects via `EVALUATOR_DB` (`postgresql+psycopg://…@db:5432/…`); `evaluator/db.py` creates tables on startup via SQLModel. Fully opt-in — unset `EVALUATOR_DB` ⇒ stateless as before. | M | Foundational |
| **User accounts** ✅ | **DONE (Google OAuth).** Sign-in via Authlib + signed-cookie sessions; `User` model keyed by Google `sub` (`evaluator/models.py` / `accounts.py`); `/login`, `/auth/google/callback`, `/logout` + a top-right auth widget. Additive (basic-auth untouched); lights up only when `GOOGLE_CLIENT_ID/SECRET` + DB are set. **Next:** gate saved data behind login. | M | High |
| **Saved scenarios** ✅ | **DONE.** `Scenario` table (`models.py`) + ownership-scoped CRUD (`scenarios.py`); a "Save scenario" panel on the results page (captures current slider state), a `/scenarios` list page, and open / rename / delete. Reopen restores all inputs via `/evaluate?…&sid=`. | M | High |
| **Scenario history / "my runs"** ✅ | **DONE.** `RunHistory` table; every signed-in `/evaluate` is auto-recorded (deduped vs the last run, pruned to 50). `/history` page lists them with Open / Save-as-scenario. | S–M | Medium |
| **Compare scenarios** ✅ | **DONE.** Tick 2–4 on `/scenarios` → `/compare?ids=`: a metrics table per scenario + an overlay Plotly chart of each one's buyer-minus-renter net-worth gap over time. | M | High |
| **Shareable result links** ✅ | **DONE.** `SharedResult` table; a **Share** button mints `/r/<slug>` (short token), a read-only results page anyone can open without an account. | S | Medium |
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
| **Live mortgage rates** ✅ | **DONE.** `evaluator/live.py` fetches the 5yr GoC benchmark yield from the **Bank of Canada Valet API** and derives the discounted fixed rate (+spread); cached (12h TTL) with offline fallback. `--live` / `EVALUATOR_LIVE_DATA` (web defaults on). | S–M | Medium |
| **Neighbourhood stats** | Price trends, days-on-market, price-to-rent ratio, school/transit scores for the area. | M | Medium |
| **More regions** | ✅ **Ontario done.** `data.REGION_TIERS` has 10 researched Ontario CMAs (Ottawa, Hamilton, KW, London, Windsor, Oshawa, Barrie, Kingston, Guelph, St. Catharines–Niagara) routed by FSA prefix, each with its real 2025 municipal property-tax rate. **Next:** other provinces with correct land-transfer rules (BC PTT, no LTT in AB/SK, etc.). | M (ongoing) | High |

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

### All-Ontario coverage

Today only the four North York FSAs (`M2H/M2J/M2K/M2N`) have researched values;
every other Ontario postal code falls through to `NATIONAL_DEFAULTS`. Expanding to
all of Ontario is **easier than it looks**, because the most fiddly piece is
already done:

- **Land-transfer tax is already province-correct.** `tax.land_transfer_tax()`
  always charges the **Ontario provincial LTT** and only *adds* the **Toronto
  municipal LTT** when `region == "toronto"`. So for any non-Toronto Ontario
  address the LTT and closing costs are already right — only the appreciation /
  rent / property-tax *assumptions* are still national fallbacks.
- **What's actually region-specific in `data.py`:** appreciation, current rent +
  rent growth, property-tax rate, and (optionally) local mortgage/insurance
  nuances. Everything else (S&P 500 CAGR, mortgage rate, tax brackets) is not
  geographic.

Two ways to cover Ontario, cheapest first:

1. **Province tier (S, do-now).** ✅ **DONE.** `data.ONTARIO_DEFAULTS` sits
   between Toronto and national (Ontario-wide appreciation, an Ontario rent
   figure, ~1.1% property tax, `ltt_region="ontario"`). Routing: explicit North
   York FSAs → researched Toronto data; any other `M` FSA → City of Toronto
   (keeps Toronto property tax + municipal LTT); `K/L/N/P` → `ONTARIO_DEFAULTS`;
   everything else → national. Instantly better than the Canada-wide proxy for
   every Ontario user.
2. **Per-CMA tiers (M, ongoing).** ✅ **DONE** for the 10 biggest Ontario
   markets — `data.REGION_TIERS` (Ottawa, Hamilton, Kitchener–Waterloo, London,
   Windsor, Oshawa, Barrie, Kingston, Guelph, St. Catharines–Niagara), each
   keyed by FSA prefix with the municipality's actual 2025 residential
   property-tax rate, blended SFH rents, and long-run appreciation. Routing
   handles FSA collisions via 3-char overrides (L4M/L4N → Barrie so York Region
   isn't misrouted; N1R/S/T → Cambridge/KW). Smaller CMAs (Brantford,
   Peterborough, Sudbury, Thunder Bay…) still fall back to the Ontario default.

### Is live data possible? (yes — for the *assumptions*, mostly not for listings)

The macro inputs that drive the verdict **can** be pulled live from free, official
sources; individual MLS listings essentially cannot without paid/brokerage access.

| Input | Live source (free, legit) | Feasibility |
|-------|---------------------------|-------------|
| Mortgage rates | **Bank of Canada Valet API** (posted/benchmark rates, JSON) | Easy — real API, just fetch + cache daily |
| Bond yields (to derive fixed-rate trend) | Bank of Canada Valet API | Easy |
| Appreciation trend | **Teranet–National Bank HPI**, StatCan New Housing Price Index | Medium — periodic download, region-level |
| Average rents | **CMHC HMIP** (rents by CMA / zone / bedroom) | Medium — annual data, downloadable, not real-time |
| Property-tax rate | Municipal open-data / published rate tables (MPAC assesses, municipality sets rate) | Medium — no single API; compile a table per municipality |
| Individual listings (price/beds/sqft) | CREA DDF (brokerage membership) only; portals = ToS risk | Hard / gated — keep as a later, carefully-sourced feature |

**Practical shape:** a small `live/` fetcher module with **per-source caching**
(e.g. SQLite or a JSON cache with TTLs — rates daily, rents/HPI/tax annually),
falling back to the baked-in `data.py` values when a fetch fails. Mortgage rates
via the Bank of Canada Valet API are the best first target: smallest, most
volatile, real API, immediately visible to users. Aggregate rents/HPI/tax improve
the *defaults* per region but change slowly, so live-fetching them is lower
priority than just researching them once into the Ontario/CMA tiers above.

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
| **DB backups** | If/when stateful, automate backups of the Postgres volume (`pg_dump` on a schedule, or snapshot the named volume). | S | Medium |

---

## Theme 6 — Monetization & growth

> How this becomes something people *pay* for. The tool is already a credible,
> Canada-specific buy-vs-rent engine — the monetizable gap is turning a one-shot
> calculator into (a) a personalized, authoritative **product** for consumers and
> (b) a **lead-gen / white-label tool** for real-estate and finance pros, who have
> far higher willingness to pay. Most items here build on Themes 1–5 (accounts,
> live data, reports), so monetization is mostly *packaging* existing roadmap work.

### Who would pay (and why)

| Segment | Why they pay | Willingness |
|---------|--------------|-------------|
| **Home buyers / renters** | A personalized, trustworthy "should I buy?" report for the biggest financial decision of their life | Low individually, high volume → freemium / one-time report |
| **Realtors / mortgage brokers** | Client-education + **lead capture**, white-labeled with their branding; closes deals faster | **High** (B2B SaaS) |
| **Financial advisors / planners** | A client-facing, Canada-accurate analysis they can hand over | Medium–High |
| **Banks / lenders / portals** | Embed the calculator on their site (engagement, lead funnel) | High (licensing) |
| **Fintech developers** | Programmatic access to the calculation engine | Medium (API) |

### Revenue models (rank by fit)

1. **Freemium consumer (B2C).** Free single scenario + interactive charts; **paid**
   unlocks the premium hooks below. Convert via a one-time **"full report" purchase**
   (~$5–15) or a low monthly **Plus** plan. Needs accounts + Stripe (Theme 1).
2. **Pro / white-label SaaS (B2B) — highest revenue per user.** Monthly plan for
   agents/brokers/advisors: their logo + colors, a shareable branded link/embed,
   **lead capture** (prospect enters scenario → pro gets the contact), unlimited
   scenarios, all regions. ~$30–99/mo.
3. **Affiliate / referral (often the biggest line for these tools).** Surface
   **live mortgage rates** (Theme 2) and "get pre-approved" / "find an agent" CTAs;
   earn per qualified click/lead from lenders, brokers, and brokerages. Pure
   upside, no user friction — but disclose clearly and keep the analysis unbiased.
4. **Embed / licensing.** License the widget to a brokerage, bank, or listing
   portal for a flat annual fee (their branding, their domain).
5. **API access.** Charge fintechs for the projection/tax engine (the `evaluator`
   package is already cleanly separable — see "keep the engine pure").
6. **Lead generation marketplace.** With explicit consent (CASL), sell
   qualified, intent-rich leads ("buying in M2J around $1M") to vetted local
   agents/brokers. Lucrative but the most privacy-sensitive — handle carefully.

### Premium feature hooks (what sits behind the paywall)

These make the free tier genuinely useful while giving real reasons to upgrade —
most are existing roadmap items, now framed as paid value:

- **Branded PDF / email report** (Theme 4) — the natural one-time purchase; also
  the pro deliverable. "Download your full report."
- **Save, compare & track scenarios** (Theme 1) — buy-now vs wait, two
  neighbourhoods, 20% vs 35% down, side by side.
- **Address-level analysis** (Theme 2) — real comps, comparable rents, auto-filled
  price/tax for a specific listing. High perceived value.
- **All regions / provinces** — free tier limited to a few FSAs; paid unlocks
  nationwide with correct local LTT/tax rules.
- **Confidence & risk** — Monte Carlo ranges (Theme 3) + the sensitivity grid as
  "how robust is this verdict?" — sounds authoritative, justifies payment.
- **Affordability & mortgage stress test** (new) — max purchase price, B-20 stress
  test, CMHC premium, pre-approval estimate. Strong buyer intent → great for
  affiliate/lead tie-ins.
- **Alerts** — email when rates move or the verdict flips for a saved scenario
  (recurring engagement → retention).
- **White-label + lead capture** (pro tier).

### Pricing sketch (starting point, validate later)

| Tier | Price | For | Includes |
|------|-------|-----|----------|
| **Free** | $0 | Everyone | 1 scenario, interactive charts, what-if sliders, 1 region |
| **Plus** | ~$7/mo or ~$12 one-time report | Serious buyers | Save/compare, branded PDF, all regions, Monte Carlo, address autofill, alerts |
| **Pro** | ~$39–99/mo | Agents, brokers, advisors | White-label, shareable branded link/embed, lead capture, unlimited, priority |
| **API / Embed** | Custom | Fintech, portals, banks | Engine API or licensed widget |

### What's required to enable monetization (foundations)

- **Accounts + billing:** Theme 1 accounts + **Stripe** (subscriptions + one-time);
  entitlement checks gating premium features. A feature-flag/plan layer.
- **Trust & compliance (table stakes once money changes hands):** prominent
  **"informational, not financial advice"** framing, Terms of Service + privacy
  policy, **PIPEDA** (data) and **CASL** (email/leads) compliance, transparent
  affiliate disclosure, and clear sourcing of every assumption (the
  methodology/transparency page from Theme 5).
- **Conversion surface:** a clean free→paid upgrade flow, a landing page that sells
  the value, and analytics on where users drop off (Theme 1 analytics).

### Highest-ROI sequence to first revenue

1. **Branded PDF report behind a one-time charge** — smallest lovable paid thing;
   needs accounts + Stripe + the report feature only.
2. **Affiliate mortgage-rate / "find a pro" CTAs** — revenue with no paywall and
   minimal build; pairs with live rates.
3. **Pro white-label + lead capture** — the high-value B2B tier once accounts and
   branding exist.

---

## Suggested phasing

A pragmatic order that front-loads value and respects dependencies:

**Phase 1 — Finish the model + de-risk (low effort, high trust)**
- ✅ Sensitivity grid (#6) — done (Chart 6). Terminal value (#5) — out of scope.
- ✅ Live mortgage rates — done (Bank of Canada Valet, Theme 2).
- ✅ CMHC default insurance for <20% down — done (`tax.cmhc_insurance`; premium
  financed into the loan, PST up front, >$1.5M / below-minimum-down rejected).
- Automated tests + CI for the engine. *(remaining)*
- Methodology/transparency page + clearer disclaimers. *(remaining)*

**Phase 2 — Go stateful (the platform shift)**
- ✅ PostgreSQL in a separate container (volume-mounted) + real accounts (Google OAuth).
- ✅ Saved scenarios (name, save, list, reopen, rename, delete).
- ✅ Run history ("my runs"), compare scenarios, shareable result links.
- Basic usage analytics / admin dashboard. *(next)*

**Phase 3 — Live market data (the wow factor)**
- Improve assumptions from free aggregate sources (CMHC rents, Teranet/StatCan
  trends, municipal tax tables) + more regions.
- Address autofill + comparable rentals.
- Nearby listings (only with a properly-sourced/legal data feed).

**Phase 4 — Polish & engagement**
- ✅ Interactive charts (Plotly) + what-if sliders — done.
- PDF/email reports, PWA, bilingual.
- Monte Carlo simulation.

**Phase 5 — Monetize (see Theme 6)**
- Accounts + Stripe; branded PDF report as the first paid unlock.
- Affiliate mortgage-rate / "find a pro" CTAs (revenue with no paywall).
- Pro white-label + lead capture tier for agents/brokers/advisors.
- ToS, privacy policy, affiliate disclosure, PIPEDA/CASL compliance.

> Note: Phase 5 mostly *packages* Phases 1–4 (accounts, live data, reports) into
> paid tiers — so building those well is the real groundwork for revenue.

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
- **Monetization trust:** charging money raises the bar — unbiased analysis even
  with affiliate links, honest disclaimers, transparent assumptions/sourcing, and
  no dark patterns. Credibility is the product; protect it (see Theme 6).
