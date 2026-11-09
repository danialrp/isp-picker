# Architecture

## Goal

Let Chrome, and only Chrome, use a network connection other than the system default, and switch back with one click. Other apps and the Mac's routing table are never changed.

## Pieces

### Extension (`extension/`)

- Manifest V3 service worker (`background.js`).
- **On**: asks the helper to select a connection (`POST /select`), then sets `chrome.proxy.settings` to `socks5://127.0.0.1:<socks_port>` (or a PAC script in "only listed domains" mode). With the WebRTC option on, it also sets `webRTCIPHandlingPolicy` to `disable_non_proxied_udp`.
- **Off**: clears the proxy and WebRTC settings. Chrome is back on the system route.
- Every `pollMinutes` it calls `GET /status` and colors the toolbar icon: blue = off, green = on and healthy, red = helper or connection missing.
- Connections are stored by a stable id (the macOS hardware port name, or `phone:<serial>`), so a re-plugged device keeps its selection even if its interface name changes.

### Helper (`helper/isp_picker.py`)

One asyncio process with two listeners on `127.0.0.1`:

- **SOCKS5** (`socks_port`). Supports `CONNECT` with domain, IPv4 and IPv6 targets. Chrome sends host names, so DNS happens in the helper.
- **Control API** (`control_port`). Small HTTP/1.1 JSON API used by the extension.

For a normal interface:

1. Resolve the host with UDP DNS sockets bound to the interface (`IP_BOUND_IF`). Servers, in order: the interface's DHCP DNS, its gateway, `dns_servers`. Answers are cached up to `dns_cache_max_ttl`.
2. Connect with a TCP socket bound to the interface. IPv4 first, then IPv6 if the interface has a global IPv6 address.
3. Pipe bytes both ways.

macOS keeps a scoped default route for every interface that has a router, so a bound socket leaves through that interface even when it is not the primary one, and even when a system VPN captures the default route.

For a phone (`kind: "phone"`):

1. `Phones.ensure` makes sure the agent binary on the phone matches `agent/build/agent-arm64`, starts it with the phone's DNS servers, and keeps `adb forward tcp:<local> tcp:<remote>` in place.
2. The helper opens a SOCKS5 `CONNECT` to the forwarded port and pipes bytes. Host names are resolved on the phone.
3. If the agent stops answering, the helper restarts it once and retries.

The selection is saved in `~/.config/isp-picker/state.json`, so it survives a helper restart.

### Agent (`agent/main.go`)

Static Linux arm64 binary, standard library only. SOCKS5 `CONNECT` server on the phone's loopback address. It uses Go's resolver pointed at the DNS servers passed with `-dns`, because Android has no `/etc/resolv.conf`. It runs as the adb shell user, which has network access.

## Failure behavior

- If the chosen connection is down, the helper answers SOCKS requests with "network unreachable". Chrome shows an error page. Traffic never falls back to another connection by itself.
- If the helper is down, Chrome cannot connect while a connection is selected. The icon turns red. **System default** always works because it only clears Chrome's proxy.
