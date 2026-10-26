# Security policy

## Reporting a vulnerability

Please do not open a public issue for security problems. Use GitHub's
[private vulnerability reporting](https://github.com/Scrooge-777/omni/security/advisories/new)
for this repository. You can expect a first reply within a few days.

## Supported versions

Only the latest release receives fixes.

## How OMNI protects you

OMNI runs a small web server on your own PC, so it is built to resist other websites open in your browser:

- It listens on `127.0.0.1` only, never on the network.
- Every `/api` call needs a random per-run token. Other websites cannot read it, and cannot send the custom header it travels in without a CORS preflight, which the server never approves.
- Requests whose `Host` header is not `localhost` or `127.0.0.1` are refused (blocks DNS rebinding).
- The live WebSocket also checks the browser's `Origin`.
- Strict Content-Security-Policy (`default-src 'self'`, no inline script, `frame-ancestors 'none'`), plus `nosniff`, `no-referrer`, and same-origin resource and opener policies.
- No outside scripts, fonts or analytics. The dashboard works offline. Telemetry and history stay in `%APPDATA%\OMNI`.

Actions that change the system are limited and reversible where possible:

- Power tweaks change only on-battery values, save the originals first, and can be restored.
- Cleanup is scan-first, deletes only files older than the age you set, never follows links or junctions, and skips files in use.
- Ending a process refuses Windows-critical processes, OMNI itself and anything on your protected list.

## Known limits

- Anything running on your PC as your user can read the token from the dashboard page, as it can read any of your files. OMNI does not defend against malware that already runs as you.
- Some readings (RAM map commands, some fan sources) need administrator rights. OMNI asks for nothing it does not need and works without them.
