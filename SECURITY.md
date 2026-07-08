# Security

This is a small, personal, non-commercial project, but it follows secure
development practices:

## Supply chain
- Python dependencies are pinned with hashes (`pip-compile --generate-hashes`)
  and installed with `--require-hashes` in CI.
- `pip-audit` gates every CI run; Dependabot watches pip and GitHub Actions
  weekly.
- All GitHub Actions are pinned to full commit SHAs.
- The only frontend dependency (uPlot) is vendored into the repo — no CDN,
  no third-party requests at runtime.

## Secrets
- The `data.gov.in` API key lives only in GitHub Actions Secrets and is read
  from the environment. The contract check (`e2e/validate_contract.py`)
  asserts nothing key-shaped appears in published JSON.
- The CEDA token is used only for the one-time local backfill and is never
  stored.

## Site
- Static site, no cookies, no analytics, no user input surfaces.
- Strict CSP (`default-src 'self'`, no inline scripts).
- All strings derived from upstream data are inserted with `textContent`,
  never `innerHTML` — upstream data is treated as untrusted input.

## Workflows
- `permissions` are default-deny; each job gets the minimum it needs
  (`contents: write` only on the data-commit job, `pages: write` only on
  deploy).

## Data integrity
- Rows failing sanity checks are quarantined with a reason
  (`data/quarantine/`), never silently dropped or silently kept.

## Reporting
Open a GitHub issue (or a private security advisory) on this repository.
