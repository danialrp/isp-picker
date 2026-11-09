#!/usr/bin/env python3
"""
ISP Picker helper (macOS).

A SOCKS5 proxy on 127.0.0.1 whose outgoing TCP connections and DNS lookups are
pinned to one chosen network interface (IP_BOUND_IF), plus a small HTTP control
API used by the Chrome extension. Only Chrome uses it (through the extension's
proxy setting); the rest of the Mac keeps its normal routing.

Usage:
  isp_picker.py serve            run the proxy and control API (default)
  isp_picker.py list             print the interfaces it can use
  isp_picker.py check <iface>    print the public IP seen through <iface>
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import random
import re
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

VERSION = "1.0.0"

IP_BOUND_IF = getattr(socket, "IP_BOUND_IF", 25)
IPV6_BOUND_IF = getattr(socket, "IPV6_BOUND_IF", 125)

HOME = Path(os.environ.get("ISP_PICKER_HOME", "~/.config/isp-picker")).expanduser()

DEFAULT_CONFIG = {
    "listen_host": "127.0.0.1",
    "listen_backlog": 1024,           # pending connections queue (the OS may cap it)
    "socks_port": 10871,
    "control_port": 10872,
    # Interfaces never offered (regex on the device name).
    "hide_interfaces": r"^(lo\d|gif\d|stf\d|awdl\d|llw\d|anpi\d|ap\d|bridge\d+|vmenet\d+)$",
    # DNS: the interface's own DHCP DNS first, then its gateway, then these.
    "dns_use_interface_dns": True,
    "dns_use_gateway": True,
    "dns_servers": [],
    "dns_timeout": 2.5,
    "dns_cache_max_ttl": 300,
    "dns_attempts": 3,                # tries per DNS server before moving to the next
    "dns_stale_seconds": 3600,        # reuse an expired answer this long if every server fails
    "dns_cache_max_entries": 5000,
    "dns_system_fallback": True,      # use the system resolver when an interface has no DNS
    "ipv6": True,
    "connect_timeout": 15,          # total, across all addresses of a host
    "connect_attempt_timeout": 5,   # per address
    "interface_cache_seconds": 3,
    # Close proxied connections when the selection changes, so Chrome reconnects
    # through the new interface right away.
    "close_on_switch": True,
    # Seconds a connection may stay half-closed before both sides are closed.
    "half_close_timeout": 20,
    # Open file limit the helper asks for at startup (each proxied connection uses two).
    "max_open_files": 8192,
    # Used only by the "public IP" check button.
    "ip_check_url": "https://api.ipify.org",
    # Address used to find which interface the Mac really sends traffic through.
    "route_probe": "1.1.1.1",
    # Android phones over adb (USB debugging): a small agent runs on the phone and
    # Chrome's traffic goes through it, so no network interface is added on the Mac.
    "adb_enabled": True,
    "adb_path": "",                     # empty = search PATH and the usual install folders
    "agent_binary": "",                 # empty = <project>/agent/build/agent-arm64
    "agent_remote_path": "/data/local/tmp/isp-picker-agent",
    "agent_remote_port": 18080,
    "agent_local_port_base": 10880,
    "agent_dns_servers": [],            # empty = DNS of the phone's own default network
    "agent_check_seconds": 20,
    "phone_label_suffix": " (adb)",
}

PROJECT_DIR = Path(__file__).resolve().parent.parent

log = logging.getLogger("isp-picker")


# --------------------------------------------------------------------------- config / state

def load_config(path: Path) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text()))
        except Exception as e:  # keep running on a broken file, but say so
            log.error("config %s is not valid JSON, using defaults: %s", path, e)
    return cfg


class State:
    """Persisted selection: an interface id (device name or hardware port name) or None."""

    def __init__(self, path: Path):
        self.path = path
        self.selected: str | None = None
        try:
            self.selected = json.loads(path.read_text()).get("selected")
        except Exception:
            pass

    def set(self, value: str | None):
        self.selected = value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"selected": value}))
        except Exception as e:
            log.warning("cannot save state: %s", e)


# --------------------------------------------------------------------------- interfaces

def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""


def _parse_ifconfig(text: str) -> dict:
    ifs, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z0-9]+): flags=\w+<([^>]*)>", line)
        if m:
            cur = {"name": m[1], "flags": m[2].split(","), "ipv4": [], "ipv6": [], "status": None}
            ifs[m[1]] = cur
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("inet "):
            cur["ipv4"].append(s.split()[1])
        elif s.startswith("inet6 "):
            addr = s.split()[1].split("%")[0]
            if not addr.lower().startswith("fe80") and addr != "::1" and "deprecated" not in s:
                cur["ipv6"].append(addr)
        elif s.startswith("status:"):
            cur["status"] = s.split(":", 1)[1].strip()
    return ifs


def _parse_hw_ports(text: str) -> dict:
    res, port = {}, None
    for line in text.splitlines():
        if line.startswith("Hardware Port:"):
            port = line.split(":", 1)[1].strip()
        elif line.startswith("Device:") and port:
            res[line.split(":", 1)[1].strip()] = port
            port = None
    return res


def _route_iface(dest: str) -> str | None:
    m = re.search(r"interface:\s*(\S+)", _run(["route", "-n", "get", dest]))
    return m[1] if m else None


def _ipconfig_opt(dev: str, opt: str) -> list[str]:
    out = _run(["ipconfig", "getoption", dev, opt]).strip()
    return [x for x in re.split(r"[\s,]+", out) if x]


class Phones:
    """Android phones reached over adb, each running the SOCKS agent from agent/."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.ports: dict[str, int] = {}       # serial -> local forwarded port
        self.verified: dict[str, float] = {}  # serial -> monotonic time of last good check
        self.names: dict[str, str] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self._adb: str | None = None

    def adb(self) -> str | None:
        if self._adb is None:
            cands = [self.cfg["adb_path"]] if self.cfg["adb_path"] else []
            cands += [shutil.which("adb") or "", "/opt/homebrew/bin/adb", "/usr/local/bin/adb",
                      str(Path("~/Library/Android/sdk/platform-tools/adb").expanduser())]
            self._adb = next((c for c in cands if c and os.access(c, os.X_OK)), "")
        return self._adb or None

    def run(self, serial: str | None, *args: str, timeout: float = 15) -> str:
        adb = self.adb()
        if not adb:
            raise RuntimeError("adb not found")
        cmd = [adb] + (["-s", serial] if serial else []) + list(args)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout

    def list(self) -> list[dict]:
        if not self.cfg["adb_enabled"] or not self.adb():
            return []
        try:
            out = self.run(None, "devices", timeout=5)
        except Exception:
            return []
        items = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 2:
                continue
            serial, state = parts[0], parts[1]
            if serial not in self.names and state == "device":
                try:
                    name = (self.run(serial, "shell", "getprop", "ro.product.marketname", timeout=5).strip()
                            or self.run(serial, "shell", "getprop", "ro.product.model", timeout=5).strip())
                except Exception:
                    name = ""
                self.names[serial] = name or serial
            label = self.names.get(serial, serial) + self.cfg["phone_label_suffix"]
            items.append({
                "name": f"adb:{serial}", "id": f"phone:{serial}", "hardware_port": label,
                "kind": "phone", "serial": serial, "index": None, "up": state == "device",
                "ipv4": [], "ipv6": [], "gateway": None, "dns": [],
            })
        return items

    # ---- agent lifecycle (sync, run in a worker thread)

    def _phone_dns(self, serial: str) -> list[str]:
        if self.cfg["agent_dns_servers"]:
            return list(self.cfg["agent_dns_servers"])
        out = self.run(serial, "shell", "dumpsys", "connectivity")
        m = re.search(r"Active default network: (\d+)", out)
        if not m:
            return []
        for line in out.splitlines():
            if f"NetworkAgentInfo{{network{{{m[1]}}}" in line:
                d = re.search(r"DnsAddresses: \[([^\]]*)\]", line)
                if d:
                    return [x for x in re.findall(r"/?([0-9a-fA-F:.]+)", d[1]) if "." in x]
        return []

    def _ensure_sync(self, serial: str) -> int:
        port = self.ports.setdefault(serial, self.cfg["agent_local_port_base"] + len(self.ports))
        if time.monotonic() - self.verified.get(serial, 0) < self.cfg["agent_check_seconds"] and _socks_alive(port):
            return port
        remote, rport = self.cfg["agent_remote_path"], self.cfg["agent_remote_port"]
        local_bin = Path(self.cfg["agent_binary"] or PROJECT_DIR / "agent/build/agent-arm64")
        want_md5 = hashlib.md5(local_bin.read_bytes()).hexdigest()
        have_md5 = (self.run(serial, "shell", f"md5sum {remote} 2>/dev/null").split() or [""])[0]
        dns = ",".join(self._phone_dns(serial))
        args = f"-listen 127.0.0.1:{rport} -dns {dns}" if dns else f"-listen 127.0.0.1:{rport}"
        running = self.run(serial, "shell", f"pidof {Path(remote).name}").strip()
        old_args = self.run(serial, "shell", f"cat {remote}.args 2>/dev/null").strip()
        if have_md5 != want_md5 or (running and old_args != args):
            if running:
                self.run(serial, "shell", f"kill {running}")
                running = ""
            if have_md5 != want_md5:
                self.run(serial, "push", str(local_bin), remote, timeout=60)
                self.run(serial, "shell", f"chmod 755 {remote}")
        if not running:
            self.run(serial, "shell", f"echo '{args}' > {remote}.args; "
                                      f"setsid nohup {remote} {args} >{remote}.log 2>&1 </dev/null &")
            time.sleep(0.8)
            log.info("started phone agent on %s (dns: %s)", serial, dns or "system")
        self.run(serial, "forward", f"tcp:{port}", f"tcp:{rport}")
        if not _socks_alive(port):
            raise ConnectionError(f"phone agent on {serial} does not answer")
        self.verified[serial] = time.monotonic()
        return port

    async def ensure(self, serial: str) -> int:
        lock = self.locks.setdefault(serial, asyncio.Lock())
        async with lock:
            return await asyncio.to_thread(self._ensure_sync, serial)


def _socks_alive(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as s:
            s.sendall(b"\x05\x01\x00")
            return s.recv(2) == b"\x05\x00"
    except OSError:
        return False


class UpstreamError(Exception):
    def __init__(self, code: int, msg: str = ""):
        super().__init__(msg or f"upstream SOCKS error {code}")
        self.code = code


async def socks5_connect(port: int, host: str, dport: int, timeout: float):
    """Open a CONNECT through a SOCKS5 server on 127.0.0.1:port (the phone agent)."""
    r, w = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 5)
    try:
        try:
            ip = ipaddress.ip_address(host)
            addr = (b"\x01" if ip.version == 4 else b"\x04") + ip.packed
        except ValueError:
            hb = host.encode() if host.isascii() else host.encode("idna")
            addr = b"\x03" + bytes([len(hb)]) + hb
        w.write(b"\x05\x01\x00" + b"\x05\x01\x00" + addr + struct.pack("!H", dport))
        await w.drain()
        if await asyncio.wait_for(r.readexactly(2), timeout) != b"\x05\x00":
            raise UpstreamError(1, "agent refused the handshake")
        head = await asyncio.wait_for(r.readexactly(4), timeout)
        skip = {1: 4, 4: 16}.get(head[3])
        if skip is None:
            skip = (await r.readexactly(1))[0]
        await r.readexactly(skip + 2)
        if head[1] != 0:
            raise UpstreamError(head[1])
        return r, w
    except BaseException:
        w.close()
        raise


class Interfaces:
    def __init__(self, cfg: dict, phones: Phones | None = None):
        self.cfg = cfg
        self.phones = phones
        self._cache: dict | None = None
        self._ts = 0.0

    def snapshot(self, force: bool = False) -> dict:
        if not force and self._cache and time.monotonic() - self._ts < self.cfg["interface_cache_seconds"]:
            return self._cache
        hide = re.compile(self.cfg["hide_interfaces"]) if self.cfg.get("hide_interfaces") else None
        raw = _parse_ifconfig(_run(["ifconfig"]))
        hw = _parse_hw_ports(_run(["networksetup", "-listallhardwareports"]))
        items = []
        for name, i in raw.items():
            if hide and hide.match(name):
                continue
            up = "UP" in i["flags"] and "RUNNING" in i["flags"] and i["status"] != "inactive"
            if not (i["ipv4"] or i["ipv6"]):
                continue
            port = hw.get(name)
            gateway = _ipconfig_opt(name, "router")
            dns = _ipconfig_opt(name, "domain_name_server")
            try:
                index = socket.if_nametoindex(name)
            except OSError:
                continue
            items.append({
                "name": name,
                "id": port or name,
                "hardware_port": port,
                "kind": "vpn" if name.startswith(("utun", "ipsec", "ppp")) else
                        "wifi" if port and "wi-fi" in port.lower() else "wired",
                "index": index,
                "up": up,
                "ipv4": i["ipv4"],
                "ipv6": i["ipv6"],
                "gateway": gateway[0] if gateway else None,
                "dns": dns,
            })
        if self.phones:
            items += self.phones.list()
        snap = {
            "interfaces": items,
            "default_route": _route_iface("default"),
            "egress": _route_iface(self.cfg["route_probe"]),
        }
        self._cache, self._ts = snap, time.monotonic()
        return snap

    def find(self, ident: str | None, force: bool = False) -> dict | None:
        if not ident:
            return None
        return self._match(self.snapshot(force), ident)

    def find_cached(self, ident: str | None) -> tuple[bool, dict | None]:
        """Look up in the last snapshot without running any command. (has_cache, iface)."""
        if not ident or self._cache is None:
            return self._cache is not None, None
        return True, self._match(self._cache, ident)

    @staticmethod
    def _match(snap: dict, ident: str) -> dict | None:
        for i in snap["interfaces"]:
            if ident in (i["name"], i["id"]):
                return i
        return None


# --------------------------------------------------------------------------- DNS (bound to the interface)

class DNSError(Exception):
    pass


class HostNotFound(DNSError):
    pass


def _qname(name: str) -> bytes:
    try:
        enc = name.rstrip(".").encode("idna")
    except UnicodeError:
        enc = name.rstrip(".").encode("ascii", "ignore")
    out = b""
    for label in enc.split(b"."):
        if not label or len(label) > 63:
            raise HostNotFound(name)
        out += bytes([len(label)]) + label
    return out + b"\x00"


def _skip_name(data: bytes, off: int) -> int:
    while True:
        if off >= len(data):
            raise DNSError("truncated name")
        n = data[off]
        if n == 0:
            return off + 1
        if n & 0xC0 == 0xC0:
            return off + 2
        off += 1 + n


def _parse_answer(data: bytes, qid: int, qtype: int):
    if len(data) < 12:
        raise DNSError("short reply")
    rid, flags, qd, an, _ns, _ar = struct.unpack("!HHHHHH", data[:12])
    if rid != qid:
        raise DNSError("id mismatch")
    rcode = flags & 0xF
    off = 12
    for _ in range(qd):
        off = _skip_name(data, off) + 4
    addrs, ttl_min = [], None
    for _ in range(an):
        off = _skip_name(data, off)
        rtype, _cls, ttl, rdlen = struct.unpack("!HHIH", data[off:off + 10])
        off += 10
        rdata = data[off:off + rdlen]
        off += rdlen
        if rtype == 1 and qtype == 1 and rdlen == 4:
            addrs.append(socket.inet_ntop(socket.AF_INET, rdata))
        elif rtype == 28 and qtype == 28 and rdlen == 16:
            addrs.append(socket.inet_ntop(socket.AF_INET6, rdata))
        else:
            continue
        ttl_min = ttl if ttl_min is None else min(ttl_min, ttl)
    return rcode, addrs, ttl_min or 60


class Resolver:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.cache: dict = {}
        self.inflight: dict = {}

    def servers_for(self, iface: dict) -> list[str]:
        out = []
        if self.cfg["dns_use_interface_dns"]:
            out += iface["dns"]
        if self.cfg["dns_use_gateway"] and iface["gateway"]:
            out.append(iface["gateway"])
        out += self.cfg["dns_servers"]
        seen, res = set(), []
        for s in out:
            try:
                if ipaddress.ip_address(s).version == 4 and s not in seen:
                    seen.add(s)
                    res.append(s)
            except ValueError:
                pass
        return res

    async def _query(self, server: str, name: str, qtype: int, ifindex: int):
        loop = asyncio.get_running_loop()
        qid = random.randrange(65536)
        packet = struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0) + _qname(name) + struct.pack("!HH", qtype, 1)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.IPPROTO_IP, IP_BOUND_IF, ifindex)
            s.setblocking(False)
            await loop.sock_sendto(s, packet, (server, 53))
            deadline = loop.time() + self.cfg["dns_timeout"]
            while True:
                left = deadline - loop.time()
                if left <= 0:
                    raise DNSError(f"timeout from {server}")
                data, src = await asyncio.wait_for(loop.sock_recvfrom(s, 4096), left)
                if src[0] != server:
                    continue
                try:
                    return _parse_answer(data, qid, qtype)
                except DNSError as e:
                    if "id mismatch" in str(e):
                        continue
                    raise
        finally:
            s.close()

    async def resolve(self, host: str, iface: dict) -> list[str]:
        """Resolve once per host and interface even when many requests ask at the same time."""
        key = (host.lower(), iface["name"])
        task = self.inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._resolve(host, iface))
            self.inflight[key] = task
            task.add_done_callback(lambda _t: self.inflight.pop(key, None))
        return await asyncio.shield(task)

    async def _resolve(self, host: str, iface: dict) -> list[str]:
        try:
            ipaddress.ip_address(host)
            return [host]
        except ValueError:
            pass
        key = (host.lower(), iface["name"])
        hit = self.cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        servers = self.servers_for(iface)
        if not servers:
            # Interfaces without their own DNS (VPN tunnels such as utun*) use the
            # system resolver, which on macOS already follows the VPN's DNS settings.
            if not self.cfg["dns_system_fallback"]:
                raise DNSError(f"no DNS server for {iface['name']}")
            try:
                infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
            except socket.gaierror as e:
                raise HostNotFound(host) from e
            addrs = list(dict.fromkeys(i[4][0] for i in infos))
            self.cache[key] = (time.monotonic() + 60, addrs)
            return addrs
        qtypes = [1] + ([28] if self.cfg["ipv6"] and iface["ipv6"] else [])
        last = None
        for server in [x for x in servers for _ in range(max(1, self.cfg["dns_attempts"]))]:
            results = await asyncio.gather(
                *(self._query(server, host, qt, iface["index"]) for qt in qtypes), return_exceptions=True)
            addrs, ttls, nx = [], [], False
            for r in results:
                if isinstance(r, Exception):
                    last = r
                    continue
                rcode, a, ttl = r
                if rcode == 3:
                    nx = True
                addrs += a
                if a:
                    ttls.append(ttl)
            if addrs:
                ttl = min(min(ttls), self.cfg["dns_cache_max_ttl"])
                if len(self.cache) >= self.cfg["dns_cache_max_entries"]:
                    self.cache.clear()
                self.cache[key] = (time.monotonic() + ttl, addrs)
                return addrs
            if nx:
                raise HostNotFound(host)
        # All servers failed: a recently expired answer is better than an error page.
        if hit and time.monotonic() - hit[0] < self.cfg["dns_stale_seconds"]:
            return hit[1]
        raise DNSError(f"cannot resolve {host}: {last!r}")


# --------------------------------------------------------------------------- bound TCP connect

async def connect_bound(addrs: list[str], port: int, iface: dict, timeout: float, attempt: float,
                        ssl_ctx=None, server_hostname=None):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    v4 = [a for a in addrs if ":" not in a]
    v6 = [a for a in addrs if ":" in a] if iface["ipv6"] else []
    last = None
    for addr in v4 + v6:
        left = deadline - loop.time()
        if left <= 0:
            break
        fam = socket.AF_INET6 if ":" in addr else socket.AF_INET
        s = socket.socket(fam, socket.SOCK_STREAM)
        try:
            if fam == socket.AF_INET:
                s.setsockopt(socket.IPPROTO_IP, IP_BOUND_IF, iface["index"])
            else:
                s.setsockopt(socket.IPPROTO_IPV6, IPV6_BOUND_IF, iface["index"])
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.setblocking(False)
            is_last = addr == (v4 + v6)[-1]
            await asyncio.wait_for(loop.sock_connect(s, (addr, port)), left if is_last else min(attempt, left))
            return await asyncio.open_connection(sock=s, ssl=ssl_ctx, server_hostname=server_hostname)
        except Exception as e:
            last = e
            s.close()
    raise last or TimeoutError("connect timeout")


# --------------------------------------------------------------------------- the app

class App:
    def __init__(self, cfg: dict, state: State):
        self.cfg = cfg
        self.state = state
        self.phones = Phones(cfg)
        self.ifaces = Interfaces(cfg, self.phones)
        self.resolver = Resolver(cfg)
        self.conns: set = set()
        self.stats = {"total": 0, "failed": 0, "bytes_up": 0, "bytes_down": 0}
        self.started = time.time()

    # ---------------- SOCKS5

    async def handle_socks(self, reader, writer):
        peer = writer.get_extra_info("peername")
        remote_w = None
        try:
            ver, n = await asyncio.wait_for(reader.readexactly(2), 10)
            if ver != 5:
                return
            await reader.readexactly(n)
            writer.write(b"\x05\x00")
            ver, cmd, _rsv, atyp = await reader.readexactly(4)
            if atyp == 1:
                host = socket.inet_ntop(socket.AF_INET, await reader.readexactly(4))
            elif atyp == 3:
                ln = (await reader.readexactly(1))[0]
                host = (await reader.readexactly(ln)).decode("ascii", "replace")
            elif atyp == 4:
                host = socket.inet_ntop(socket.AF_INET6, await reader.readexactly(16))
            else:
                return await self._socks_reply(writer, 8)
            port = struct.unpack("!H", await reader.readexactly(2))[0]
            if cmd != 1:
                return await self._socks_reply(writer, 7)

            self.stats["total"] += 1
            # interface discovery runs system commands: keep it off the event loop
            # The background refresher keeps the snapshot current, so the connection
            # path never waits for system commands. Fall back to a fresh lookup only
            # when there is no snapshot yet or the selection is missing from it.
            has_cache, iface = self.ifaces.find_cached(self.state.selected)
            if not has_cache or iface is None:
                iface = await asyncio.to_thread(self.ifaces.find, self.state.selected, True)
            if not iface or not iface["up"]:
                self.stats["failed"] += 1
                log.warning("%s:%s refused: interface %r is not available", host, port, self.state.selected)
                return await self._socks_reply(writer, 3)
            try:
                remote_r, remote_w = await self.open_upstream(iface, host, port)
            except UpstreamError as e:
                self.stats["failed"] += 1
                return await self._socks_reply(writer, e.code)
            except HostNotFound:
                self.stats["failed"] += 1
                return await self._socks_reply(writer, 4)
            except ConnectionRefusedError:
                self.stats["failed"] += 1
                return await self._socks_reply(writer, 5)
            except Exception as e:
                self.stats["failed"] += 1
                log.info("%s:%s via %s failed: %s", host, port, iface["name"], repr(e))
                return await self._socks_reply(writer, 4)

            await self._socks_reply(writer, 0)
            entry = (writer, remote_w)
            self.conns.add(entry)
            try:
                up = asyncio.ensure_future(self._pipe(reader, remote_w, "bytes_up"))
                down = asyncio.ensure_future(self._pipe(remote_r, writer, "bytes_down"))
                _, pending = await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
                # One side finished: give the other a short grace period, then close both,
                # so half-closed sockets do not pile up.
                if pending:
                    _, pending = await asyncio.wait(pending, timeout=self.cfg["half_close_timeout"])
                    for t in pending:
                        t.cancel()
            finally:
                self.conns.discard(entry)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError, OSError):
            pass
        except Exception:
            log.exception("socks session from %s", peer)
        finally:
            for w in (writer, remote_w):
                if w is not None:
                    try:
                        w.close()
                    except Exception:
                        pass

    async def _socks_reply(self, writer, code: int):
        writer.write(bytes([5, code, 0, 1, 0, 0, 0, 0, 0, 0]))
        try:
            await writer.drain()
        except Exception:
            pass

    async def _pipe(self, reader, writer, counter: str):
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                self.stats[counter] += len(data)
                writer.write(data)
                await writer.drain()
        except (ConnectionError, OSError, asyncio.CancelledError):
            pass
        finally:
            try:
                if writer.can_write_eof():
                    writer.write_eof()
            except Exception:
                pass

    def close_all(self):
        for a, b in list(self.conns):
            for w in (a, b):
                try:
                    w.close()
                except Exception:
                    pass
        self.conns.clear()

    # ---------------- control API

    def status(self, force: bool = False) -> dict:
        snap = self.ifaces.snapshot(force)
        sel = self.ifaces.find(self.state.selected)
        return {
            "version": VERSION,
            "socks_port": self.cfg["socks_port"],
            "selected": self.state.selected,
            "selected_interface": sel["name"] if sel else None,
            "selected_up": bool(sel and sel["up"]),
            "active_connections": len(self.conns),
            "stats": self.stats,
            "uptime": int(time.time() - self.started),
            **snap,
        }

    async def open_upstream(self, iface: dict, host: str, port: int):
        """TCP connection to host:port leaving through the chosen interface or phone."""
        if iface["kind"] == "phone":
            timeout = self.cfg["connect_timeout"]
            local = await self.phones.ensure(iface["serial"])
            try:
                return await socks5_connect(local, host, port, timeout)
            except (ConnectionError, OSError, asyncio.IncompleteReadError):
                # agent died or the forward was dropped: restart it once
                self.phones.verified.pop(iface["serial"], None)
                local = await self.phones.ensure(iface["serial"])
                return await socks5_connect(local, host, port, timeout)
        addrs = await self.resolver.resolve(host, iface)
        return await connect_bound(addrs, port, iface, self.cfg["connect_timeout"],
                                   self.cfg["connect_attempt_timeout"])

    async def public_ip(self, ident: str) -> dict:
        iface = await asyncio.to_thread(self.ifaces.find, ident, True)
        if not iface:
            raise ValueError(f"interface {ident!r} not found")
        u = urlsplit(self.cfg["ip_check_url"])
        https = u.scheme == "https"
        port = u.port or (443 if https else 80)
        t0 = time.monotonic()
        r, w = await self.open_upstream(iface, u.hostname, port)
        if https:
            await w.start_tls(ssl.create_default_context(), server_hostname=u.hostname)
        try:
            path = (u.path or "/") + (f"?{u.query}" if u.query else "")
            w.write(f"GET {path} HTTP/1.1\r\nHost: {u.hostname}\r\nUser-Agent: isp-picker/{VERSION}\r\n"
                    f"Accept: text/plain\r\nConnection: close\r\n\r\n".encode())
            await w.drain()
            raw = await asyncio.wait_for(r.read(65536), self.cfg["connect_timeout"])
        finally:
            w.close()
        head, _, body = raw.partition(b"\r\n\r\n")
        if b"chunked" in head.lower():
            size, _, rest = body.partition(b"\r\n")
            body = rest[:int(size.strip() or b"0", 16)]
        return {"interface": iface["name"], "ip": body.decode(errors="replace").strip()[:100],
                "ms": int((time.monotonic() - t0) * 1000)}

    async def handle_control(self, reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            lines = head.decode("latin-1").split("\r\n")
            method, target, _ = lines[0].split(" ", 2)
            headers = {}
            for l in lines[1:]:
                if ":" in l:
                    k, v = l.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            length = int(headers.get("content-length") or 0)
            if length > 65536:
                return await self._send(writer, 413, {"error": "too large"})
            body = await reader.readexactly(length) if length else b""

            # Only this Mac's own tools and Chrome extensions may talk to the API:
            # reject foreign Host headers (DNS rebinding) and web-page origins (CSRF).
            host = headers.get("host", "")
            host = host[1:host.index("]")] if host.startswith("[") else host.split(":")[0]
            if host not in ("127.0.0.1", "localhost", "::1"):
                return await self._send(writer, 403, {"error": "bad host"})
            origin = headers.get("origin")
            if origin and not origin.startswith("chrome-extension://"):
                return await self._send(writer, 403, {"error": "bad origin"})

            u = urlsplit(target)
            q = dict(p.split("=", 1) for p in u.query.split("&") if "=" in p)
            if method == "GET" and u.path == "/status":
                return await self._send(writer, 200, await asyncio.to_thread(self.status, q.get("fresh") == "1"))
            if method == "POST" and u.path == "/select":
                data = json.loads(body or b"{}")
                ident = data.get("interface") or None
                if ident and not await asyncio.to_thread(self.ifaces.find, ident, True):
                    return await self._send(writer, 404, {"error": f"interface {ident!r} not found"})
                changed = ident != self.state.selected
                self.state.set(ident)
                if changed and self.cfg["close_on_switch"]:
                    self.close_all()
                log.info("selected interface: %s", ident or "(none)")
                return await self._send(writer, 200, await asyncio.to_thread(self.status))
            if method == "GET" and u.path == "/check":
                try:
                    return await self._send(writer, 200, await self.public_ip(q.get("interface", "")))
                except Exception as e:
                    return await self._send(writer, 502, {"error": str(e) or type(e).__name__})
            return await self._send(writer, 404, {"error": "not found"})
        except Exception as e:
            try:
                await self._send(writer, 400, {"error": str(e)})
            except Exception:
                pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _send(self, writer, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode()
        writer.write(f"HTTP/1.1 {code} X\r\nContent-Type: application/json; charset=utf-8\r\n"
                     f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n".encode()
                     + body)
        await writer.drain()

    async def serve(self):
        h = self.cfg["listen_host"]
        socks = await asyncio.start_server(self.handle_socks, h, self.cfg["socks_port"], backlog=self.cfg["listen_backlog"])
        ctrl = await asyncio.start_server(self.handle_control, h, self.cfg["control_port"])
        log.info("isp-picker %s: SOCKS5 %s:%s, control http://%s:%s, selected=%s",
                 VERSION, h, self.cfg["socks_port"], h, self.cfg["control_port"], self.state.selected)
        async with socks, ctrl:
            await asyncio.gather(socks.serve_forever(), ctrl.serve_forever(), self._refresh_loop())

    async def _refresh_loop(self):
        while True:
            try:
                await asyncio.to_thread(self.ifaces.snapshot, True)
            except Exception as e:
                log.warning("interface refresh failed: %s", e)
            await asyncio.sleep(self.cfg["interface_cache_seconds"])


def raise_fd_limit(want: int):
    """launchd starts agents with a 256 file limit; a browser opens far more connections."""
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        target = want if hard == resource.RLIM_INFINITY else min(want, hard)
        if soft < target:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    except Exception as e:
        log.warning("cannot raise the open file limit: %s", e)


def main():
    ap = argparse.ArgumentParser(description="Pin Chrome's traffic to one network interface.")
    ap.add_argument("command", nargs="?", default="serve", choices=["serve", "list", "check"])
    ap.add_argument("interface", nargs="?")
    ap.add_argument("--config", default=str(HOME / "config.json"))
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(Path(a.config).expanduser())
    raise_fd_limit(cfg["max_open_files"])
    app = App(cfg, State(HOME / "state.json"))

    if a.command == "list":
        print(json.dumps(app.status(force=True), indent=2, ensure_ascii=False))
    elif a.command == "check":
        if not a.interface:
            ap.error("check needs an interface name, e.g. en0")
        print(json.dumps(asyncio.run(app.public_ip(a.interface)), indent=2))
    else:
        try:
            asyncio.run(app.serve())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
