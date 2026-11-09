# Notes for AI agents and new developers

Read [README.md](README.md) first. This file lists what you need to change code safely.

## Map

| You want to change | Look at |
|---|---|
| Popup UI or its text | `extension/popup.html`, `popup.js`, `popup.css`, `_locales/en/messages.json` |
| How Chrome's proxy is set or cleared | `extension/background.js` (`applyProxy`, `clearProxy`, `enable`, `disable`) |
| Interface discovery | `helper/isp_picker.py` → `Interfaces.snapshot` |
| DNS for an interface | `helper/isp_picker.py` → `Resolver` |
| Pinning sockets to an interface | `helper/isp_picker.py` → `connect_bound` |
| Phone (adb) mode | `helper/isp_picker.py` → `Phones`, `socks5_connect`; `agent/main.go` |
| Control API | `helper/isp_picker.py` → `App.handle_control` |
| Defaults | `DEFAULT_CONFIG` in the helper, `DEFAULTS` in `background.js` |

## Rules

- macOS only for the helper. It relies on `IP_BOUND_IF` / `IPV6_BOUND_IF` and on `ifconfig`, `networksetup`, `ipconfig`, `route`.
- Never change the Mac's network settings or routes from code or tests. The design depends on touching only Chrome's proxy setting.
- Standard library only in `helper/` and `agent/`.
- Keep the control API bound to localhost and keep its Origin and Host checks.

## Checks

See the command block in [CONTRIBUTING.md](CONTRIBUTING.md). CI runs the same steps (`.github/workflows/ci.yml`).
