const $ = (id) => document.getElementById(id);
const send = (msg) => chrome.runtime.sendMessage(msg);
const t = (key, subs) => chrome.i18n.getMessage(key, subs) || key;

const KIND = { wifi: "kindWifi", wired: "kindWired", vpn: "kindVpn", phone: "kindPhone" };
const SHORTCUT_FALLBACK = "Alt+Shift+K";

let state = null;
let busy = false;
let editing = null;
const ipResults = {};

function translate(root) {
  root.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  root.querySelectorAll("[data-i18n-title]").forEach((el) => { el.title = t(el.dataset.i18nTitle); });
}

function errorText(code) {
  if (code === "helper-unreachable") return t("errHelper");
  if (code === "iface-down") return t("errIfaceDown");
  if (code === "iface-missing") return t("errIfaceMissing");
  if (code.startsWith("proxy-locked")) return t("errProxyLocked");
  return code;
}

function showAlert(code) {
  const box = $("alert");
  if (!code) { box.hidden = true; return; }
  box.hidden = false;
  box.textContent = errorText(code);
  if (code === "helper-unreachable") {
    const c = document.createElement("code");
    c.textContent = t("errHelperCmd");
    box.append(c);
  }
}

const label = (iface) => state.settings.labels[iface.id] || iface.hardware_port || iface.name;

function render() {
  const { settings: s, status } = state;
  const active = s.active;
  const ifaces = status?.interfaces || [];
  const activeIface = ifaces.find((i) => i.id === active);

  // header
  const now = $("now");
  now.className = "now";
  if (!active) {
    $("nowTitle").textContent = t("statusDefault");
    $("nowSub").textContent = status?.egress ? t("statusEgress", [status.egress]) : "";
  } else {
    $("nowTitle").textContent = t("statusVia", [activeIface ? label(activeIface) : (s.labels[active] || active)]);
    if (!status) { now.classList.add("error"); $("nowSub").textContent = t("statusHelperDown"); }
    else if (!activeIface || !activeIface.up) { now.classList.add("error"); $("nowSub").textContent = t("statusIfaceDown"); }
    else {
      now.classList.add("on");
      const conns = t("statusOpenConns", [String(status.active_connections)]);
      $("nowSub").textContent = activeIface.kind === "phone" ? conns : `${activeIface.name} · ${conns}`;
    }
  }

  // default row
  $("defaultBtn").classList.toggle("active", !active);
  $("defaultTag").hidden = !!active;
  $("defaultMeta").textContent = status?.egress ? t("defaultRowMeta", [status.egress]) : "";

  // connection rows
  const list = $("list");
  list.replaceChildren();
  if (status && !ifaces.length) list.textContent = t("noConnections");
  for (const i of ifaces) {
    const row = $("rowTpl").content.firstElementChild.cloneNode(true);
    const isActive = i.id === active;
    row.classList.toggle("active", isActive);
    row.classList.toggle("down", !i.up);
    row.querySelector(".row-name").textContent = label(i);
    const meta = [i.kind === "phone" ? "" : i.name, KIND[i.kind] ? t(KIND[i.kind]) : ""];
    if (!i.up) meta.push(t("down"));
    if (status.egress === i.name) meta.push(t("systemRoute"));
    row.querySelector(".row-meta").textContent = meta.filter(Boolean).join(" · ");
    const ip = ipResults[i.id];
    row.querySelector(".row-ip").textContent =
      ip ? (ip.error ? t("ipError") : t("publicIp", [ip.ip, String(ip.ms)])) : (i.ipv4[0] || i.ipv6[0] || "");

    row.querySelector(".tag").hidden = !isActive;
    const use = row.querySelector(".use");
    use.hidden = isActive;
    use.disabled = busy || !i.up;
    use.onclick = () => act({ type: "enable", id: i.id });

    const check = row.querySelector(".check");
    check.disabled = !i.up;
    check.onclick = async () => {
      check.disabled = true; check.textContent = "…";
      const r = await send({ type: "check", id: i.id });
      ipResults[i.id] = r.result || { error: r.error };
      render();
    };

    const nameEl = row.querySelector(".row-name");
    const input = row.querySelector(".rename");
    const startEdit = () => {
      editing = i.id;
      nameEl.hidden = true; input.hidden = false; input.value = label(i); input.focus(); input.select();
    };
    const finish = async (save) => {
      if (editing !== i.id) return;
      editing = null;
      if (save) await send({ type: "rename", id: i.id, label: input.value });
      await load();
    };
    row.querySelector(".edit").onclick = startEdit;
    input.onkeydown = (e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); };
    input.onblur = () => finish(true);
    if (editing === i.id) startEdit();

    list.append(row);
  }

  // settings form (left alone while the user is typing in it)
  const f = $("form");
  if (!f.contains(document.activeElement)) {
    f.mode.value = s.mode;
    f.domains.value = s.domains.join("\n");
    f.bypass.value = s.bypass.join("\n");
    f.webrtcGuard.checked = s.webrtcGuard;
    f.helperHost.value = s.helperHost;
    f.controlPort.value = s.controlPort;
    f.pollMinutes.value = s.pollMinutes;
  }
}

async function load() {
  state = await send({ type: "state" });
  showAlert(state.error || (state.proxyLock ? `proxy-locked:${state.proxyLock}` : null));
  if (editing === null) render();
}

async function act(msg) {
  busy = true; render();
  const r = await send(msg);
  busy = false;
  await load();
  if (r?.error) showAlert(r.error);
}

$("defaultBtn").onclick = () => act({ type: "disable" });

const lines = (v) => v.split(/[\n,]+/).map((x) => x.trim()).filter(Boolean);
$("form").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target;
  const r = await send({ type: "saveSettings", patch: {
    mode: f.mode.value,
    domains: lines(f.domains.value),
    bypass: lines(f.bypass.value),
    webrtcGuard: f.webrtcGuard.checked,
    helperHost: f.helperHost.value.trim() || "127.0.0.1",
    controlPort: Number(f.controlPort.value) || 10872,
    pollMinutes: Number(f.pollMinutes.value) || 1,
  } });
  document.activeElement?.blur();
  await load();
  if (r?.error) showAlert(r.error);
};
$("resetBtn").onclick = async () => { await send({ type: "resetSettings" }); document.activeElement?.blur(); await load(); };

translate(document);
translate($("rowTpl").content);
chrome.commands?.getAll?.().then((cmds) => {
  const key = cmds.find((c) => c.name === "toggle")?.shortcut || SHORTCUT_FALLBACK;
  $("shortcutHint").textContent = t("shortcutHint", [key]);
}).catch(() => {});
load();
setInterval(() => { if (!busy && editing === null && !$("form").contains(document.activeElement)) load(); }, 3000);
