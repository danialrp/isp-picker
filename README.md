# ISP Picker for Chrome

A Chrome extension for macOS that sends **only Chrome's traffic** through a network connection you pick: Wi-Fi, Ethernet, USB tethering, a VPN interface, or an Android phone's mobile data over a USB cable (adb). The rest of the Mac keeps using its normal connection.

One click turns it on. One click on **System default** turns it off and Chrome goes back to the Mac's normal route.

No measurable speed cost, about 11 MB of memory when idle, and 27 of 27 option checks passing. See [Benchmarks and test results](#benchmarks-and-test-results).

## Why

A Mac has one default route. Sometimes you want a single app, here Chrome, to use a different line: a phone's mobile data to reach sites that block your main network, or plain Wi-Fi while a system VPN is running. macOS has no per-app routing switch, so this project adds one for Chrome.

## How it works

```
Chrome ──(proxy setting)──▶ helper on 127.0.0.1:10871 (SOCKS5)
                               │
                               ├─▶ Wi-Fi / Ethernet / tethering / VPN interface
                               │     (socket pinned to the interface with IP_BOUND_IF,
                               │      DNS asked through the same interface)
                               │
                               └─▶ Android phone over adb
                                     (adb forward → small SOCKS5 agent on the phone →
                                      the phone's own internet and DNS)
```

There are three parts:

| Part | Folder | What it does |
|---|---|---|
| Chrome extension | `extension/` | Popup to pick a connection. Sets Chrome's proxy to the helper while a connection is chosen and clears it for **System default**. |
| Helper (macOS) | `helper/` | Python 3 program, no dependencies. Runs a local SOCKS5 proxy (`127.0.0.1:10871`) and a small control API (`127.0.0.1:10872`). Pins each outgoing connection to the chosen interface. |
| Phone agent (optional) | `agent/` | Tiny Go SOCKS5 server that the helper copies to an Android phone and starts over adb. Used for "phone over USB" mode. |

Nothing changes in the Mac's network settings. The extension only touches Chrome's proxy setting, and only while a connection is selected.

## Requirements

- macOS (Apple Silicon or Intel)
- Google Chrome (or another Chromium browser that loads unpacked extensions)
- Python 3.11 or newer (`python3 --version`). No packages to install.
- For phone mode: `adb` ([Android platform-tools](https://developer.android.com/tools/releases/platform-tools)), Go 1.21+ to build the agent, and an Android phone with USB debugging turned on

## Install

1. Clone the repo:

   ```bash
   git clone https://github.com/danialrp/isp-picker.git
   cd isp-picker
   ```

2. Install the helper. It runs as a LaunchAgent, starts at login and restarts if it stops. The script also builds the phone agent when Go is available.

   ```bash
   bash scripts/install.sh
   ```

3. Load the extension in Chrome:
   - Open `chrome://extensions`
   - Turn on **Developer mode** (top right)
   - Click **Load unpacked** and pick the `extension` folder

4. Click the ISP Picker icon in the toolbar. You should see your connections listed.

Optional environment variables for `install.sh`: `PYTHON` (Python to use), `ISP_PICKER_HOME` (config folder, default `~/.config/isp-picker`), `ISP_PICKER_LABEL` (LaunchAgent label, default `local.isp-picker`).

To remove the helper later: `bash scripts/uninstall.sh`. Remove the extension from `chrome://extensions`.

## Use

- **Use** on a connection: Chrome's traffic goes through it. The toolbar icon turns green.
- **System default**: Chrome goes back to the Mac's normal route. The icon turns blue.
- **IP**: shows the public IP address seen through that connection.
- **Rename**: give a connection your own name (saved in the extension).
- A red icon means the chosen connection or the helper is not available. Chrome requests fail instead of leaking through another connection.
- Keyboard shortcut **Alt+Shift+K** switches between System default and the last connection you used. Change it at `chrome://extensions/shortcuts`.

### Settings (in the popup)

| Setting | Default | Meaning |
|---|---|---|
| Which sites | All sites | **Only the domains below** sends just the listed domains (and their subdomains) through the chosen connection. Everything else stays direct. |
| Domains | empty | One domain per line, for example `example.com`. A top-level domain such as `org` also works. |
| Always direct | `<local>`, `localhost`, `127.0.0.1`, `::1`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16` | Hosts and IP ranges that never use the chosen connection. |
| WebRTC | on | Stops WebRTC from using UDP outside the proxy while a connection is chosen. |
| Helper address / Control port | `127.0.0.1` / `10872` | Where the extension finds the helper. |
| Status check | 1 minute | How often the toolbar icon re-checks the connection. |

## Phone over USB (adb)

Plug in an Android phone with USB debugging on and accept the "Allow USB debugging" prompt. The phone appears in the list as `<phone name> (adb)`.

When you pick it, the helper:

1. copies `agent/build/agent-arm64` to `/data/local/tmp/isp-picker-agent` on the phone (only when it changed),
2. starts it with the DNS servers of the phone's current network,
3. runs `adb forward tcp:10880 tcp:18080`,
4. sends Chrome's connections through it.

The phone's own internet (mobile data or its Wi-Fi) carries the traffic. No network interface is added to the Mac and no tethering is needed. USB debugging must stay on while you use this mode.

Why not plain USB tethering? It works too: tethering shows up as a normal interface you can pick. On some phones it does not, because macOS has no RNDIS driver and some Android builds break NCM tethering. The adb mode avoids both problems.

## Helper configuration

The install script creates an empty `~/.config/isp-picker/config.json` (`{}`). Add only the keys you want to change; the rest use the defaults listed below and in [`helper/config.example.json`](helper/config.example.json).

| Key | Default | Meaning |
|---|---|---|
| `listen_host` | `127.0.0.1` | Address both helper ports listen on. Keep it on localhost. |
| `listen_backlog` | `1024` | Queue size for pending connections (macOS may cap it). |
| `socks_port` | `10871` | Local SOCKS5 port Chrome uses. |
| `control_port` | `10872` | Local control API port the extension uses. |
| `hide_interfaces` | see example | Regex of interface names never listed (loopback, AirDrop, bridges...). |
| `dns_use_interface_dns` | `true` | Ask the interface's own DHCP DNS servers first. |
| `dns_use_gateway` | `true` | Then ask the interface's gateway. |
| `dns_servers` | `[]` | Then ask these extra servers. |
| `dns_timeout` | `2.5` | Seconds to wait for one DNS answer. |
| `dns_cache_max_ttl` | `300` | Longest time, in seconds, a DNS answer is cached. |
| `dns_attempts` | `3` | Tries per DNS server before moving to the next one. |
| `dns_stale_seconds` | `3600` | If every DNS server fails, reuse an expired answer up to this many seconds old. |
| `dns_cache_max_entries` | `5000` | DNS cache size. The cache is cleared when it is full. |
| `dns_system_fallback` | `true` | Interfaces with no DNS of their own (VPN tunnels) use the system resolver. |
| `ipv6` | `true` | Use IPv6 when the interface has a global IPv6 address. |
| `connect_timeout` | `15` | Seconds for a whole connect, across all addresses of a host. |
| `connect_attempt_timeout` | `5` | Seconds per address before trying the next one. |
| `interface_cache_seconds` | `3` | How often, in seconds, the interface list is refreshed in the background. |
| `close_on_switch` | `true` | Close open proxied connections when you switch, so Chrome reconnects on the new line. |
| `half_close_timeout` | `20` | Seconds a connection may stay half-closed before both sides are closed. |
| `max_open_files` | `8192` | Open file limit the helper asks for at startup (launchd's default is 256). |
| `ip_check_url` | `https://api.ipify.org` | Used only by the IP button. |
| `route_probe` | `1.1.1.1` | Address used to find which interface the Mac really sends traffic through. |
| `adb_enabled` | `true` | List Android phones found by adb. |
| `adb_path` | empty | Path to adb. Empty = search PATH and the usual install folders. |
| `agent_binary` | empty | Phone agent binary. Empty = agent/build/agent-arm64. |
| `agent_remote_path` | `/data/local/tmp/isp-picker-agent` | Where the agent is copied on the phone. |
| `agent_remote_port` | `18080` | Port the agent listens on, on the phone's loopback address. |
| `agent_local_port_base` | `10880` | First local port used for adb forward (one port per phone). |
| `agent_dns_servers` | `[]` | Force DNS servers for the phone agent. Empty = the DNS of the phone's own network. |
| `agent_check_seconds` | `20` | How long a successful agent health check is trusted. |
| `phone_label_suffix` | ` (adb)` | Text added to a phone's name in the list. |

Restart the helper after editing: `launchctl kickstart -k gui/$(id -u)/local.isp-picker`

Logs: `~/Library/Logs/isp-picker.log`

## Command line

```bash
python3 helper/isp_picker.py list           # show the connections the helper can use
python3 helper/isp_picker.py check en0      # public IP through one interface
python3 helper/isp_picker.py serve          # run in the foreground (the LaunchAgent does this)
```

The control API answers only on `127.0.0.1` and only to local tools or Chrome extensions:

| Method | Path | Body / query | Returns |
|---|---|---|---|
| `GET` | `/status` | `?fresh=1` to skip the cache | Connections, the selected one, counters |
| `POST` | `/select` | `{"interface": "<id>"}` or `{"interface": null}` | New status |
| `GET` | `/check` | `?interface=<id>` | `{"ip": "...", "ms": 123}` |

## Troubleshooting

| Problem | Fix |
|---|---|
| Popup says the helper does not answer | Run `bash scripts/install.sh` again and check `~/Library/Logs/isp-picker.log`. |
| "Another extension controls the proxy" | Turn off other proxy or VPN extensions in Chrome. Only one extension can own the proxy setting. |
| Plugging in a phone moved the whole Mac to the phone | In **System Settings → Network → ⋯ → Set Service Order**, put your main connection above the phone. |
| Phone missing from the list | Check `adb devices`. The phone must show as `device`, not `unauthorized`. |
| A site fails only through one connection | That network may block the site. Use the **IP** button to confirm which network you are on. |

## Benchmarks and test results

Measured on a Mac with Chrome, Wi-Fi as the main connection, and an Android phone connected over USB in adb mode. Full method and raw numbers: [docs/TESTING.md](docs/TESTING.md).

### Performance

| Measurement | Through the helper | Direct (no helper) |
|---|---|---|
| HTTPS request over Wi-Fi, new connection each time (median of 25) | 374 ms | 383 ms |
| Download over Wi-Fi (50 MB) | 49 Mbit/s | 47 Mbit/s |
| 200 parallel requests over Wi-Fi, 8 rounds | 1600 of 1600 succeed | n/a |
| 32 parallel requests (Chrome's per-proxy limit), 20 rounds | 640 of 640 succeed | n/a |

The helper adds no measurable delay or throughput loss. Differences above are network noise.

### Memory

| Process | Under load | Idle after load |
|---|---|---|
| Helper (Python) | 15 to 32 MB RSS, about 41 open files | about 11 MB, no growth across rounds |
| Phone agent (Go) | 5.6 MB before, 6.6 MB after 480 requests | flat at 6.6 MB |

No leak was found: memory and open files return to the same level after every round, and no half-closed sockets remain 20 seconds after a burst.

### Startup

| Event | Time |
|---|---|
| Helper start to first API answer | about 240 ms |
| Popup open to connection list shown | about 90 ms |
| Phone agent cold start (started by the first request) | about 1.5 s, request succeeds |
| Phone agent killed while in use | restarted by the helper, the same request succeeds |

### Functional checks

| Area | Result |
|---|---|
| Extension options, automated in an isolated browser profile | 27 of 27 pass (Use, System default, IP, Rename, domain-list mode, bypass list, WebRTC on/off, status interval, wrong port alert, red badge, Reset, shortcut, no console errors) |
| Traffic through Wi-Fi, a VPN tunnel interface, and the phone | Pass |
| Sites blocked on the main network, opened through the phone | Pass |
| Unknown host, connection down, web-page Origin, foreign Host header | Correct SOCKS error codes / `403` |
| System default route during all tests | Never changed |

## Security

- The extension asks only for `proxy`, `privacy` (for the WebRTC option), `storage`, `alarms`, and access to `127.0.0.1` / `localhost`. It cannot read pages.
- The helper listens on `127.0.0.1` only.
- The control API rejects requests with a web-page `Origin` and requests whose `Host` is not `127.0.0.1` or `localhost` (blocks CSRF and DNS rebinding).
- The phone agent listens on the phone's `127.0.0.1` only and is reached through `adb forward`.

See [SECURITY.md](SECURITY.md) to report a problem.

## Project layout

```
extension/         Chrome extension (Manifest V3)
  _locales/en/     UI strings
  background.js    proxy switching, toolbar icon, health checks
  popup.*          popup UI
helper/            macOS helper (Python, standard library only)
agent/             Android phone agent (Go, standard library only)
scripts/           install, uninstall, build and packaging scripts
docs/              architecture and test notes
```

More detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/TESTING.md](docs/TESTING.md), [CONTRIBUTING.md](CONTRIBUTING.md), [AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE)
