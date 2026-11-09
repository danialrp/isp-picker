// ISP Picker: service worker.
// Chrome's proxy points at the local helper (SOCKS5) only while a connection is
// chosen; otherwise the proxy setting is cleared and Chrome uses the Mac default.

export const DEFAULTS = {
  helperHost: "127.0.0.1",
  controlPort: 10872,
  mode: "all",              // "all" = every site, "list" = only the domains below
  domains: [],
  bypass: ["<local>", "localhost", "127.0.0.1", "::1",
           "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"],
  webrtcGuard: true,        // stop WebRTC UDP from leaving through the Mac default
  labels: {},               // interface id -> display name
  active: null,             // interface id Chrome uses now; null = Mac default
  lastUsed: null,
  pollMinutes: 1,
  iconColors: { off: "#2563eb", on: "#16a34a", error: "#dc2626" },  // toolbar icon per state
  requestTimeoutMs: 4000,
};


async function getSettings() {
  const s = await chrome.storage.local.get(null);
  return { ...DEFAULTS, ...s };
}

async function saveSettings(patch) {
  await chrome.storage.local.set(patch);
  return getSettings();
}

async function api(path, { method = "GET", body } = {}) {
  const s = await getSettings();
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), path.startsWith("/check") ? 20000 : s.requestTimeoutMs);
  try {
    const res = await fetch(`http://${s.helperHost}:${s.controlPort}${path}`, {
      method,
      headers: body ? { "content-type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
      cache: "no-store",
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  } catch (e) {
    if (e.name === "AbortError" || e instanceof TypeError) {
      const err = new Error("helper-unreachable");
      err.code = "helper-unreachable";
      throw err;
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

function buildPac(host, port, domains) {
  const list = JSON.stringify(domains.map((d) => d.trim().replace(/^\*?\./, "").toLowerCase()).filter(Boolean));
  return `var D=${list};
function FindProxyForURL(url, host) {
  host = host.toLowerCase();
  for (var i = 0; i < D.length; i++) {
    if (host === D[i] || dnsDomainIs(host, "." + D[i])) return "SOCKS5 ${host}:${port}";
  }
  return "DIRECT";
}`;
}

async function proxyControllable() {
  const { levelOfControl } = await chrome.proxy.settings.get({});
  return levelOfControl === "controllable_by_this_extension" || levelOfControl === "controlled_by_this_extension"
    ? null : levelOfControl;
}

async function applyProxy(s, socksPort) {
  const blocked = await proxyControllable();
  if (blocked) throw new Error(`proxy-locked:${blocked}`);
  const value = s.mode === "list"
    ? { mode: "pac_script", pacScript: { data: buildPac(s.helperHost, socksPort, s.domains) } }
    : { mode: "fixed_servers",
        rules: { singleProxy: { scheme: "socks5", host: s.helperHost, port: socksPort }, bypassList: s.bypass } };
  await chrome.proxy.settings.set({ value, scope: "regular" });
  if (s.webrtcGuard) {
    await chrome.privacy.network.webRTCIPHandlingPolicy.set({ value: "disable_non_proxied_udp" }).catch(() => {});
  } else {
    await chrome.privacy.network.webRTCIPHandlingPolicy.clear({}).catch(() => {});
  }
}

async function clearProxy() {
  await chrome.proxy.settings.clear({ scope: "regular" });
  await chrome.privacy.network.webRTCIPHandlingPolicy.clear({}).catch(() => {});
}

function findIface(status, id) {
  return status?.interfaces?.find((i) => i.id === id || i.name === id) || null;
}

export async function enable(id) {
  const s = await getSettings();
  const status = await api("/status?fresh=1");
  const iface = findIface(status, id);
  if (!iface) throw new Error("iface-missing");
  if (!iface.up) throw new Error("iface-down");
  const after = await api("/select", { method: "POST", body: { interface: iface.id } });
  await applyProxy(s, after.socks_port);
  await saveSettings({ active: iface.id, lastUsed: iface.id });
  await refreshBadge(after);
  return after;
}

export async function disable() {
  await clearProxy();
  await saveSettings({ active: null });
  let status = null;
  try { status = await api("/select", { method: "POST", body: { interface: null } }); } catch {}
  await refreshBadge(status, true);
  return status;
}

// ---------------------------------------------------------------- toolbar icon

function drawIcon(size, color) {
  const c = new OffscreenCanvas(size, size);
  const g = c.getContext("2d");
  const r = size / 2;
  g.fillStyle = color;
  g.beginPath(); g.arc(r, r, r - 0.5, 0, Math.PI * 2); g.fill();
  g.strokeStyle = "#fff"; g.fillStyle = "#fff";
  g.lineWidth = Math.max(1.5, size / 11); g.lineCap = "round";
  const arrow = (y, dir) => {
    const x0 = size * 0.27, x1 = size * 0.73, h = size * 0.13;
    g.beginPath(); g.moveTo(x0, y); g.lineTo(x1, y); g.stroke();
    const tip = dir > 0 ? x1 + size * 0.04 : x0 - size * 0.04;
    g.beginPath(); g.moveTo(tip, y); g.lineTo(tip - dir * h * 1.3, y - h); g.lineTo(tip - dir * h * 1.3, y + h);
    g.closePath(); g.fill();
  };
  arrow(size * 0.37, 1);
  arrow(size * 0.63, -1);
  return g.getImageData(0, 0, size, size);
}

async function setLook(kind, title, badge = "") {
  const { iconColors } = await getSettings();
  const color = iconColors[kind] || DEFAULTS.iconColors[kind];
  await chrome.action.setIcon({ imageData: { 16: drawIcon(16, color), 32: drawIcon(32, color) } });
  await chrome.action.setBadgeText({ text: badge });
  if (badge) await chrome.action.setBadgeBackgroundColor({ color });
  await chrome.action.setTitle({ title });
}

async function refreshBadge(status, knownOff = false) {
  const s = await getSettings();
  if (knownOff || !s.active) return setLook("off", "ISP Picker: Mac default");
  if (!status) {
    try { status = await api("/status"); } catch { status = null; }
  }
  const label = s.labels[s.active] || s.active;
  if (!status) return setLook("error", `ISP Picker: helper is not running (${label})`, "!");
  const iface = findIface(status, s.active);
  if (!iface || !iface.up) return setLook("error", `ISP Picker: ${label} is disconnected`, "!");
  if (status.selected !== iface.id) {
    // Helper restarted or was switched elsewhere: re-assert our choice.
    try { await api("/select", { method: "POST", body: { interface: iface.id } }); } catch {}
  }
  return setLook("on", `ISP Picker: Chrome → ${label} (${iface.name})`, "ON");
}

async function boot() {
  const s = await getSettings();
  if (s.active) {
    try {
      const status = await api("/status");
      await applyProxy(s, status.socks_port);
    } catch {}
  }
  await chrome.alarms.create("health", { periodInMinutes: Math.max(0.5, s.pollMinutes) });
  await refreshBadge();
}

chrome.runtime.onInstalled.addListener(boot);
chrome.runtime.onStartup.addListener(boot);
chrome.alarms.onAlarm.addListener((a) => { if (a.name === "health") refreshBadge(); });
chrome.proxy.onProxyError.addListener((d) => console.warn("proxy error", d.error, d.details));

chrome.commands.onCommand.addListener(async (cmd) => {
  if (cmd !== "toggle") return;
  const s = await getSettings();
  try {
    if (s.active) await disable();
    else if (s.lastUsed) await enable(s.lastUsed);
  } catch (e) {
    await setLook("error", `ISP Picker: ${e.message}`, "!");
  }
});

// ---------------------------------------------------------------- popup messages

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  (async () => {
    switch (msg.type) {
      case "state": {
        const s = await getSettings();
        let status = null, error = null;
        try { status = await api("/status"); } catch (e) { error = e.code || e.message; }
        const proxyLock = await proxyControllable();
        return { settings: s, status, error, proxyLock };
      }
      case "enable": return { status: await enable(msg.id) };
      case "disable": return { status: await disable() };
      case "check": return { result: await api(`/check?interface=${encodeURIComponent(msg.id)}`) };
      case "rename": {
        const s = await getSettings();
        const labels = { ...s.labels };
        if (msg.label?.trim()) labels[msg.id] = msg.label.trim(); else delete labels[msg.id];
        await saveSettings({ labels });
        await refreshBadge();
        return {};
      }
      case "saveSettings": {
        const s = await saveSettings(msg.patch);
        if (s.active) {
          const status = await api("/status");
          await applyProxy(s, status.socks_port);
        }
        await chrome.alarms.create("health", { periodInMinutes: Math.max(0.5, s.pollMinutes) });
        return {};
      }
      case "resetSettings": {
        const keep = await getSettings();
        await chrome.storage.local.clear();
        await saveSettings({ labels: keep.labels, active: keep.active, lastUsed: keep.lastUsed });
        if (keep.active) await enable(keep.active).catch(() => {});
        return {};
      }
    }
    return { error: "unknown message" };
  })().then(reply, (e) => reply({ error: e.code || e.message }));
  return true;
});
