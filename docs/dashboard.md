# Dashboard design, release policy and verification

EpiScope’s local dashboard brings surveillance and resource planning into one coordinated workspace. Its intended users are national planners and district officers. This is a synthetic teaching prototype; it is not connected to a health authority and does not make clinical diagnoses.

## Data flow and scale

`serving/export.py` reads Spark gold marts after the pipeline and analytics stages. It never reads encounter records, patient identifiers, the facility line list or injected-outbreak truth. The principal observational release is one disjoint cell per district, disease and ISO week. At five years, four diseases and 64 districts, this produces approximately 67,000 compact rows regardless of the encounter scale factor. The remaining release consists of the latest forecast origin, latest daily signal state, latest stock-derived allocation grid, a division preparedness calendar and selected evaluation summaries. District/disease/date indices avoid repeated strings.

The exporter rejects combined `dashboard/data/*.json` above **10,000,000 bytes**. A SHA-256 release manifest records the actual bytes and row counts. Geography is simplified at approximately 0.008 degrees and rounded to four decimal places for a district-level overview. Exterior and hole winding are normalized for D3’s spherical rendering convention. The simplified geometry is a display asset, not an analytical boundary used for population aggregation.

The browser loads D3 7.9.0 from the checked-in `dashboard/vendor/` directory and fetches one aggregate payload from `/api/data`. No external chart library, CDN request or line-level endpoint is needed at runtime.

## Five coordinated views

| View | Encoding and purpose | Linked interaction |
|---|---|---|
| District choropleth | Released cases per 100,000 in the selected period; amber circular markers identify current review signals | Select a district by map or keyboard; updates the other views |
| Weekly trajectory | Released weekly cases, a transparent weekly review line, four-week predictions and interval ribbon | A five-year navigator brushes the shared date range; wheel zoom changes the same range |
| Seasonal heatmap | ISO epidemic week × ISO year, coloured by released weekly incidence | Select a cell to focus an eight-week window across the workspace |
| Resource plan | Current stock and recommended top-up against upper-bound demand; population baseline marker | Select a district; change the resource or precomputed budget/equity scenario |
| Ranked warnings | Daily EARS-C2 standardized excess, current review status, reporting activity proxy and a verification action | Select a district; the detection multiplier updates flags and map markers |

Disease tabs and a district select provide shared filters and keyboard alternatives to graphical selection. Each card has a freshness timestamp. Sequential teal colouring supports incidence comparison; orange markers, distinct shapes and explicit text distinguish warnings without relying on colour alone. The forecast uses a separate purple line and translucent band. Small screens stack cards into one column and retain a scrollable warning table inside its card. The page itself is checked for horizontal overflow at 390 pixels.

The allocation grid is intentionally tied to the **latest planning cycle**. Selecting a historical time window dims this view and explains that historical stocks have not been reconstructed. This avoids silently presenting current inventories as historical ones. Equity is the minimum fraction of each district’s population-proportional share of an allocated resource. Budget values are relative planning units using explicit teaching cost assumptions, not currency or verified procurement prices.

## Interpretation of signals and uncertainty

The warning table, map markers and review KPI share one decision rule. At the default **3σ**, they use the official EARS-C2 flag released from gold analytics, computed before the public score is rounded to two decimal places. A raw score just above three can therefore correctly display as `3.00` beside a review flag. Other multipliers use a strict **published score > selected multiplier** comparison and are labelled approximate what-if flags. Equality does not produce an alert. These what-if flags can differ from rerunning the detector on unrounded private values.

Only complete, non-suppressed signals without a suspected reporting artefact can become review flags. Suspected reporting backfill receives a separate pause status and a request to verify reporting activity, rather than an outbreak claim. Reporting activity is a relative proxy; it is not verified submission completeness.

The timeseries operates at a different, privacy-preserving frequency. Its clearly labelled **weekly review threshold** is the previous eight released weekly values’ mean plus the selected multiplier times their sample standard deviation. It is a planning aid and is not presented as the daily EARS detector’s count threshold. The warning footer reports the nearest actually evaluated detection operating point when one is available.

District forecasts display nominal 80% intervals, with measured coverage documented in the analytics outputs. At national scope, the ribbon is the sum of district lower and upper bounds. The UI explicitly states that national interval coverage is not calibrated; summing district bounds does not establish an 80% national interval. Small predicted arithmetic families can be withheld under the release policy rather than plotted as zero.

## National policy checks (AN-2 and AN-8)

An expandable **Policy checks** section below the five coordinated views contains two additional D3 plots. These are national teaching evaluations. District, disease, date and resource selections do not filter their coordinates; selecting a district within a national session does not turn them into district performance estimates. District access omits both source arrays at the server, hides the section and clears previously rendered plots and selection captions.

The detection plot shows event sensitivity against false alerts per district-year for EARS-C2 and CUSUM across all diseases and the full evaluated period. The configured grid has 14 evaluated points. Its tooltip reports the median actual issue delay among detected events, rather than an onset-only delay or a delay for missed events. A ring marks the EARS-C2 multiplier nearest to the detection control, with the lower multiplier used for an equal-distance tie. CUSUM retains its own evaluated settings. Moving the control does not invent an unevaluated operating point.

The planning plot shows the 24 budget/equity scenarios as six budget curves, connected in increasing requested equity-floor order. The horizontal coordinate is the achieved minimum district-resource allocation share divided by that district's population share, across resources with nonzero total allocation. It is distinct from the requested equity floor. The vertical coordinate is the percentage reduction in weighted unmet need versus population-based allocation at the same budget. The current budget/equity pair receives a selection ring. These summaries pool all resources in the latest national plan.

Coincident scenarios retain the same coordinates and zero benefits remain zero; no jitter or invented separation is added. Every point has a keyboard focus target and an accessible metric label. Tab exposes its tooltip, and Escape dismisses it. Null or non-finite coordinates are omitted with an unavailable message if no releasable points remain. The compact plots stack on small screens. Their shared national scope and synthetic interpretation are stated beside the charts.

## Small-cell and complementary-disclosure controls

1. Positive observational counts below five become `null`. Zero remains zero. All other public case counts are rounded **down** to multiples of five.
2. Only disjoint district/disease/week case observations are released. There are no exact national or division case totals, overlapping daily counts, cumulative totals, report counts, admissions or deaths with which to subtract and recover a hidden weekly value.
3. Displayed totals sum visible safe bins and are labelled as lower bounds when cells are withheld. A suppressed district-week interrupts its district trajectory. Suppression is never reinterpreted as absence of disease.
4. Daily signal numerator, expected count and count threshold stay private. The signal score and flag are withheld if the numerator or expected baseline is positive and below five.
5. Demand, stock, allocation, unmet demand and baseline allocation form one arithmetic family. If any positive quantity is below five, the whole tuple is withheld. Exact national unmet or allocation totals are excluded from scenario summaries. This blocks the direct `demand − stock − allocation` reconstruction path.
6. JSON count validation runs before publication. The tests inject each prohibited value from one through four and verify rejection, including a partial-suppression case.

These controls address small-cell disclosure and direct complementary subtraction. They do not establish formal differential privacy or guarantee protection against all possible external information. This limitation is stated in the application and the technical serving notes.

## Server-enforced access scopes

`python -m serving.server` binds only to loopback. With no configured tokens it provides a local national development session. When `EPISCOPE_TOKENS` is configured, `/api/data` requires an Authorization bearer token. A national principal receives the national release. A district principal receives only its district’s observations, signals, forecasts, allocation rows and geometry, plus its division’s preparedness calendar. District row indices are remapped consistently. National evaluation summaries are omitted from a district response.

Role filtering happens before JSON serialization. A query parameter cannot change the authenticated principal. Static `/data/` access is prohibited, as are arbitrary paths, directory listings, encoded traversal and lake files. Only a small asset allowlist is served. Responses use `Cache-Control: no-store`, a restrictive content security policy, `nosniff` and a no-referrer policy. Tokens are never written to source files or logged in request URLs. The optional tab session stores its own token until that tab closes.

A generic static HTTP server must not be used for role demonstrations, because it would expose the unfiltered release files. An internet deployment would require a reviewed authentication, authorization, TLS and operational architecture beyond this loopback prototype.

## Verification and honest usability status

`tests/test_serving.py` covers five-count binning, non-finite inputs, coupled suppression, complementary-total exclusions, district filtering across every published table and geometry, geometry winding, national-scale compact payload headroom and HTTP access-control bypass attempts. It verifies that a district token cannot read another district and that static JSON is unavailable without API authorization.

`tests/browser_smoke.cjs` optionally exercises the actual application in Playwright: disease tabs, district selection, detection and budget sliders, seasonal-cell time filtering, choropleth selection, required D3 charts and warning rows, absence of browser exceptions, and 390-pixel responsive overflow. Its shared `tests/browser_policy_helpers.cjs` checks 14 detection points, 24 scenario points, six budget lines, control-driven selection rings, national-coordinate invariance across linked filters, keyboard tooltips and expanded mobile layout. It can save desktop/mobile and policy-chart screenshots through `EPISCOPE_SCREENSHOT_DIR`. Fixture screenshots are explicitly labelled **UI TEST FIXTURE — NOT A SURVEILLANCE RESULT**, remain under `work/`, and must not be represented as measured project results.

`tests/browser_scope.cjs` uses a token supplied through `EPISCOPE_DISTRICT_TOKEN` to verify the token dialog, the server response’s district boundary, row-index consistency, district-only controls and reset behaviour, and the static-release bypass denial. It also verifies that national policy arrays are empty and plot DOM, legends and selected-point captions are cleared. Supplying `EPISCOPE_NATIONAL_TOKEN` additionally exercises an actual national-to-district transition in the same browser tab. It never prints either token or records one in a screenshot. Browser checks against the final release are recorded separately; preparing or syntax-checking a script does not count as an executed browser pass.

Automation and visual inspection assess functional correctness and layout. **Two non-CS human usability sessions have not been conducted.** Their names, observations, completion times and findings are not invented. A human study remains necessary before claiming the brief’s usability recommendation has been satisfied. Suitable tasks are selecting a district, explaining a forecast interval, identifying a withheld cell, adjusting the equity policy and deciding which signal requires source verification.

Technical reproduction notes and the retained D3 licence are in `serving/README.md`. Final report, policy brief and presentation production are deferred as requested.

## Per-load quality evidence (DQ-2)

The national view includes an expandable per-load quality table with protected input, accepted, quarantined and duplicate/replaced counts. Each load exposes its rule checks and approximate pass rates derived from released five-count bins. Exact private ratios are not exported. A small component suppresses the entire count balance family; district tokens receive no national load metadata. Relative reporting activity is explicitly labelled as a proxy rather than verified submission completeness.
