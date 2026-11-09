# Testing

## Automated checks

CI (`.github/workflows/ci.yml`) compiles the helper, vets and cross-builds the agent, syntax-checks the extension scripts, validates the JSON files and checks shell syntax.

## Manual test plan

| # | Step | Expected |
|---|---|---|
| 1 | `python3 helper/isp_picker.py list` | Every connected interface is listed with `up: true` |
| 2 | Select Wi-Fi, then `curl --socks5-hostname 127.0.0.1:10871 https://example.com` | `200` |
| 3 | Select an interface that is down | SOCKS reply 3, Chrome shows an error, icon red |
| 4 | Look up a host that does not exist | SOCKS reply 4 |
| 5 | `curl -H 'Origin: https://evil.example' http://127.0.0.1:10872/status` | `403 bad origin` |
| 6 | `curl -H 'Host: evil.example' http://127.0.0.1:10872/status` | `403 bad host` |
| 7 | Restart the helper | Selection is kept |
| 8 | 20 parallel requests through the proxy | All succeed |
| 9 | Phone mode: select the phone, open sites, press **IP** | Sites load, public IP differs from the Mac's |
| 10 | Throughout, `route -n get default` | Never changes |

## Results (v1.0.0)

Tested on a Mac with Chrome, Wi-Fi as the main connection, and an Android phone connected over USB in adb mode.

| Test | Result |
|---|---|
| Interface listing, selection, restart persistence | Pass |
| Through Wi-Fi: common sites | `200`, about 0.7 to 2 s |
| Pinned interface while a VPN holds the default route | Sockets leave through the pinned interface, not the VPN |
| Through the phone (adb): regional sites blocked on the Wi-Fi network | `200` / `301`, about 0.25 to 1.5 s |
| Through the phone: public IP | Differs from the Wi-Fi IP |
| 20 parallel requests (Wi-Fi and phone) | 20 of 20 succeed |
| Heavy parallel load, helper running under launchd | No file-descriptor errors, no half-closed sockets left after 20 s (details below) |
| 60 parallel requests through the phone | All answered (one `429` rate limit from the site) |
| VPN tunnel interface (no DNS of its own) | Works through the system resolver fallback |
| Unknown host, interface down, bad Origin, bad Host | Correct SOCKS codes / `403` |
| System default route during all tests | Unchanged |

### Memory, performance and startup

| Measurement | Result |
|---|---|
| Helper memory, 20 rounds x 32 parallel requests (Chrome's per-proxy limit) | 15 to 32 MB RSS, back to about 11 MB when idle. No growth across rounds |
| Helper memory, 8 rounds x 200 parallel requests | 1600 of 1600 succeed, RSS 17 to 32 MB, about 41 open files after each round |
| Phone agent memory, 15 rounds x 32 parallel requests | 5.6 MB before, 6.6 MB after, flat when idle |
| Latency through Wi-Fi (median of 25 new HTTPS connections) | 374 ms through the helper vs 383 ms direct (within noise) |
| Download through Wi-Fi (50 MB) | 49 Mbit/s through the helper vs 47 Mbit/s direct |
| Helper start to first API answer | about 240 ms |
| Phone agent cold start (first request starts it) | about 1.5 s, request succeeds |
| Phone agent killed while in use | Helper restarts it, the same request succeeds |
| Popup open to connection list shown | about 90 ms |

### Extension options (automated, isolated browser profile)

27 of 27 checks pass: connection list and English UI, **Use** sets Chrome's SOCKS5 proxy and the helper selection, WebRTC guard, badge states (`ON`, `!`, none), page loads through Wi-Fi and through the phone, **IP** button, **Rename**, "only listed domains" mode (listed domain through the helper, other domains direct), bypass list, WebRTC option off, status-check interval, wrong control port shows the helper alert, **System default** clears the proxy, **Reset to defaults** keeps names, toggle shortcut registered, no console errors.

Known limitation found during testing: on some Android phones, USB tethering in NCM mode gives the Mac an address but no working link, because Android's Ethernet service claims the `usb0` interface and clears its address. RNDIS tethering has no macOS driver. Phone mode over adb was added for this case.
