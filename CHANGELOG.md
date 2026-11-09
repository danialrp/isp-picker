# Changelog

All notable changes to this project are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] - 2026-11-09

### Added

- Chrome extension (Manifest V3) with a popup to pick the connection Chrome uses, a System default switch, public IP check, renaming, per-domain mode, bypass list, WebRTC guard and a toggle shortcut.
- macOS helper: local SOCKS5 proxy that pins connections and DNS to one interface with `IP_BOUND_IF`, plus a localhost control API.
- Android phone mode: Go SOCKS5 agent started over adb, so Chrome can use a phone's internet without USB tethering.
- LaunchAgent install and uninstall scripts.
- Reliability under load: raised open-file limit, half-close timeout, shared DNS lookups with retries and stale-answer fallback, background interface refresh, system-resolver fallback for VPN tunnels.
