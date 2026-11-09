# Security policy

## Supported versions

Only the latest release gets security fixes.

## Reporting a problem

Please do not open a public issue for security problems. Use GitHub's
**Report a vulnerability** button on the repository's Security tab to send a
private report. Include what you found, how to reproduce it, and the version.

You should get an answer within a week.

## Scope

- The helper's SOCKS5 proxy and control API, which must only be reachable from the local machine.
- The extension's handling of proxy settings.
- The phone agent and how the helper starts it over adb.

## Design notes

- Both helper ports listen on `127.0.0.1` only.
- The control API rejects a web-page `Origin` and any `Host` other than `127.0.0.1` / `localhost`.
- The phone agent listens on the phone's loopback address only and is reached through `adb forward`.
- No data leaves the machine except the traffic Chrome sends and, when you press **IP**, one request to `ip_check_url`.
