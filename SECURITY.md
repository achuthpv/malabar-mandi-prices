# Security

This is a small, personal, non-commercial project, but it follows secure
development practices. Last full audit: 2026-07-09 (pip-audit clean on
runtime + dev dependencies; no secrets in the repo; vendored uPlot matches
its npm-published sha256).

## Attack surface, honestly stated

The deployed site is **static files on GitHub Pages**: no backend, no
database connection, no cookies, no login, no user-generated content
stored anywhere. The realistic attack paths are (a) the supply chain
(dependencies, actions), (b) the GitHub account/repo itself, and
(c) malicious data from the upstream price feed. All three are addressed
below.

## Supply chain
- Python dependencies are pinned with hashes (`pip-compile --generate-hashes`)
  and installed with `--require-hashes` in CI — a tampered PyPI package
  fails the install rather than running.
- `pip-audit` gates every CI run; Dependabot watches pip and GitHub Actions
  weekly.
- All GitHub Actions are pinned to full commit SHAs, and workflow
  `permissions` are default-deny (`contents: write` only on the data-commit
  job, `pages: write`/`id-token: write` only on deploy, `contents: read`
  everywhere else).
- The only frontend dependency (uPlot 1.6.32) is vendored into the repo —
  no CDN, no third-party requests at page load. Vendored file sha256:
  `19c8d4c6ad88929a79f4ae49d6f7161566dfd0ba3d15cc495e974f787eb78f1f`.

## Repo / deployment (do these on GitHub — cannot be set from code)
- [ ] Enable branch protection on `main` (require CI to pass; restrict force-push).
- [ ] Enable two-factor authentication on the GitHub account (the account
      IS the deployment credential for a Pages site).
- [ ] Optional: enable CodeQL (free for public repos) and secret scanning.

## Secrets
- The `data.gov.in` API key lives only in GitHub Actions Secrets and is
  read from the environment. The contract check (`e2e/validate_contract.py`)
  asserts nothing key-shaped appears in published JSON.
- No other server-side secrets exist.

## Site
- Static site; strict CSP: `default-src 'self'`, no inline scripts,
  `object-src 'none'`, `base-uri 'none'`. The ONLY external host the CSP
  permits is `https://api.anthropic.com`, used exclusively by the opt-in
  demo AI mode below.
- All strings derived from upstream data or user input are inserted with
  `textContent`, never `innerHTML` — upstream feed data is treated as
  untrusted input throughout.

## "Ask about the market" and the demo AI mode
- **Default (always available):** the question box is a rule engine running
  entirely in the visitor's browser over the site's published JSON. There
  is no API to spam, no key to steal, and questions never leave the page.
- **Demo AI mode (opt-in, bring-your-own-key):** the demo-er pastes their
  own Anthropic API key at demo time.
  - The key is held in `sessionStorage` only — it dies when the tab closes
    and is never written to localStorage, cookies, the URL or any server.
  - It is sent only to `api.anthropic.com` (the CSP blocks every other
    external host), with a key-format check before enabling.
  - **Spam/abuse:** impossible by construction — there is no shared key and
    no proxy. A visitor can only spend their *own* key. A 401/403 response
    auto-disables AI mode.
  - **Prompt injection:** the model receives only the site's own published
    analysis JSON plus the question; the system prompt pins scope, marks
    the question as untrusted, and instructs refusal of off-topic or
    rule-changing requests. Responses are capped (400 tokens), rendered
    via `textContent`, and one request runs at a time. Worst case if a
    demo-er's question tricks the model: a wrong sentence appears in their
    own tab — no data, keys or accounts are reachable.
  - If a always-on public AI answerer is ever wanted, the documented path
    is a Cloudflare Worker proxy with rate limiting + Turnstile — do NOT
    embed a shared key in the client.

## Data integrity
- Rows failing sanity checks are quarantined with a reason
  (`data/quarantine/`), never silently dropped or silently kept.
- Cross-market spread excludes stale markets and price outliers so bad or
  incomparable upstream data cannot fabricate arbitrage signals.

## Reporting
Open a GitHub issue (or a private security advisory) on this repository.
