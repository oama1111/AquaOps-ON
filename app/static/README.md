# Vendored front-end assets

`htmx.min.js` is a third-party script that is committed here rather than loaded
from a public CDN. This directory exists because the operator dashboard is served
to small municipal water systems, where the network may be filtered, metered or
air-gapped, and where a third-party script host is a supply-chain dependency the
operator has no way to audit. Serving the file from the same origin also removes
a mixed-content question and a certificate-trust question.

| Field | Value |
| --- | --- |
| Package | `htmx.org` |
| Version | `1.9.12` (pinned; the dashboard previously loaded this exact version from `unpkg.com`) |
| Source | `https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js` |
| Retrieved | 2026-09-19 |
| Size | 48,101 bytes |
| SHA-256 | `449317ade7881e949510db614991e195c3a099c4c791c24dacec55f9f4a2a452` |
| Licence | BSD 2-Clause (zero-clause BSD), as declared by the upstream project |

`app/main.py` mounts this directory at `/static`, and `app/templates/dashboard.html`
loads `/static/htmx.min.js`. Nothing at runtime reaches the public internet.

## Upgrading

1. Download the new pinned version and replace `htmx.min.js`.
2. Recompute the checksum and update the table above:

   ```bash
   shasum -a 256 app/static/htmx.min.js
   ```

3. Confirm the dashboard still renders with JavaScript disabled — the three
   panels are server-rendered into the first response, so the page must remain
   readable even if this script never loads.
