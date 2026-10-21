# Aggregate release and local dashboard

From the project root, after the gold and analytics stages:

```bash
python -m serving.export --root data
python -m serving.server --root dashboard --port 8080
```

Open `http://127.0.0.1:8080`. The application runs entirely locally, including D3 7.9.0. The dashboard is not a clinical service. Public geography, population and climate provide context; all encounter-derived health observations are synthetic.

## Release policy

The browser receives only disjoint **district × disease × ISO week** case observations, latest model outputs, current aggregate stocks, precomputed allocation scenarios and division preparedness indices. Spark prepares the source marts. Five years, four diseases and 64 districts require roughly 67,000 compact weekly rows, independent of the encounter scale factor. The exporter rejects a combined JSON release above 10,000,000 bytes.

Positive case counts below five are withheld. Other released counts are rounded down to multiples of five. Zero is distinct from withheld (`null`). Daily observations, report counts, admissions, deaths, patient identifiers and exact national or division case totals never enter the release. Displayed totals sum visible bins only and therefore form lower bounds. No exact parent total or overlapping daily/cumulative observation is available to recover a hidden weekly case count by subtraction. The daily EARS-C2 score and its flag are withheld if its numerator or expected baseline is positive and below five; daily numerator, expected count and threshold are not exported. A district-week case line breaks at a suppressed cell.

Demand, stock, allocation, unmet demand and baseline allocation form an arithmetic family. If any positive member is below five, all five are withheld together; otherwise each is coarsened independently. Exact national unmet-demand or allocation totals are excluded from the scenario summaries. The release tests check both small values and complementary arithmetic leakage. This is a deliberately conservative small-cell policy, not a claim of formal differential privacy or protection against all external information.

The timeseries weekly review line is calculated from the previous eight **released** weekly values (mean plus the selected multiplier times sample standard deviation). It is a planning aid, clearly distinct from the actual daily EARS-C2 detector. The warning table and map markers use daily standardized EARS scores. The forecast ribbon always remains visible, subject to coupled suppression of tiny predicted bounds. At national scope it is the sum of district 80% interval bounds; national coverage has not been calibrated and is not claimed to be 80%.

## Access scopes

Without configured tokens, the loopback server opens a local national development session. To demonstrate enforced district access, set `EPISCOPE_TOKENS` in the shell to a JSON mapping from random tokens of at least 24 characters to principals:

```json
{
  "replace-with-a-long-random-national-token": {"role": "national"},
  "replace-with-a-long-random-district-token": {"role": "district", "district_id": "01"}
}
```

These examples are placeholders, not credentials. Keep real tokens outside the repository. Restart the server and use **Access token** in the dashboard. The token is retained only in the browser tab’s session storage and sent as an Authorization header. The server filters every district-indexed table, geometry and division calendar **before responding**. A district token cannot request another district through a query parameter. Static `/data/` access, directory listings, arbitrary files and traversal paths are rejected. Switching a CSS class is not the access-control mechanism. Tokens are optional only because this prototype binds exclusively to loopback; an internet deployment would require a separately reviewed authenticated TLS architecture.

Use this server for role demonstrations. A generic static HTTP server would expose the export files without the role checks.

## Interaction and verification

District selection in the choropleth, warning table or allocation bars coordinates every view. Disease tabs, the timeseries navigator, mouse-wheel zoom and seasonal-cell selection update the shared selection. The controls update the precomputed budget/equity grid and daily signal flags. Resource stocks remain the latest planning cycle when historical weeks are selected, and the UI explicitly labels that distinction. Every card includes the release timestamp. Selects and sliders provide keyboard alternatives to map interaction; chart tooltips, axes, safe sequential colours, uncertainty legends and mobile stacking support readability.

Budget values are relative planning units using documented teaching cost assumptions; they are not currency or verified procurement prices. Run `python -m unittest discover -s tests -p 'test_serving.py'` for disclosure and HTTP endpoint tests. Optional browser verification uses Node and Playwright: start the server, then run `node tests/browser_smoke.cjs`; set `EPISCOPE_BROWSER_CHANNEL=chrome` to use an already installed Chrome. Real non-CS usability sessions still need human participants; no participant findings are invented.

Third-party asset: `dashboard/vendor/d3.v7.min.js`, D3 7.9.0, retrieved from `https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js`. Upstream: `https://github.com/d3/d3/tree/v7.9.0`. Its ISC licence is retained in `dashboard/vendor/D3-LICENSE.txt` from `https://raw.githubusercontent.com/d3/d3/v7.9.0/LICENSE`. Application chart code is original to this project.
