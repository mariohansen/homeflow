/* Application shell: state, views and command dispatch.

   State handling follows docs/architecture/overview.md: the snapshot is the truth,
   the WebSocket carries updates, and the interface never shows a result the
   gateway has not confirmed. A command that ends UNKNOWN is displayed as
   unknown, not as success and not as failure. */

import { Api, ApiError, clearCredential, loadCredential, storeCredential } from "./api.js";
import { LiveConnection } from "./live.js";
import { applyStaticStrings, t } from "./strings.js";
import { renderActivityEntry, renderDevice, renderRoom } from "./render.js";

const HOLD_RELEASE_MS = 5000;
const TOAST_MS = 4000;

const dom = {
  connect: document.getElementById("view-connect"),
  connectForm: document.getElementById("connect-form"),
  connectToken: document.getElementById("connect-token"),
  connectSubmit: document.getElementById("connect-submit"),
  connectError: document.getElementById("connect-error"),
  shell: document.getElementById("shell"),
  title: document.getElementById("topbar-title"),
  link: document.getElementById("link-state"),
  linkLabel: document.getElementById("link-label"),
  views: {
    home: document.getElementById("view-home"),
    activity: document.getElementById("view-activity"),
    settings: document.getElementById("view-settings"),
  },
  rooms: document.getElementById("home-rooms"),
  homeEmpty: document.getElementById("home-empty"),
  activityList: document.getElementById("activity-list"),
  activityEmpty: document.getElementById("activity-empty"),
  settingsClient: document.getElementById("settings-client"),
  settingsMode: document.getElementById("settings-mode"),
  settingsHost: document.getElementById("settings-host"),
  settingsDevices: document.getElementById("settings-devices"),
  signOut: document.getElementById("sign-out"),
  toast: document.getElementById("toast"),
  tabs: [...document.querySelectorAll(".tab")],
};

const state = {
  api: null,
  live: null,
  identity: null,
  devices: new Map(),
  busy: new Set(),
  held: new Map(),
  notices: new Map(),
  view: "home",
};

/* --- Command context passed to the renderer ------------------------------ */

const ctx = {
  isBusy: (deviceId) => state.busy.has(deviceId),
  noticeFor: (deviceId) => state.notices.get(deviceId) ?? null,
  holdRender: (deviceId) => holdRender(deviceId),
  execute: (device, action, parameters) => execute(device, action, parameters),
};

function holdRender(deviceId) {
  const existing = state.held.get(deviceId);
  if (existing) window.clearTimeout(existing);
  state.held.set(
    deviceId,
    window.setTimeout(() => state.held.delete(deviceId), HOLD_RELEASE_MS),
  );
}

function releaseHold(deviceId) {
  const timer = state.held.get(deviceId);
  if (timer) window.clearTimeout(timer);
  state.held.delete(deviceId);
}

async function execute(device, action, parameters) {
  state.busy.add(device.id);
  state.notices.delete(device.id);
  patchDevice(device.id, { force: true });

  try {
    const command = await state.api.submitCommand(device.id, action, parameters);
    if (command.status === "UNKNOWN") {
      state.notices.set(device.id, { kind: "unknown", text: t("command.unknown") });
    } else if (command.status !== "SUCCEEDED") {
      state.notices.set(device.id, {
        kind: "error",
        text: t("command.failed"),
      });
    }
  } catch (error) {
    const message = error instanceof ApiError ? error.localizedMessage : t("error.internal_error");
    state.notices.set(device.id, { kind: "error", text: message });
    if (error instanceof ApiError && error.type === "unauthenticated") {
      signOut();
      return;
    }
  } finally {
    state.busy.delete(device.id);
    releaseHold(device.id);
  }

  // The command response reflects the device; refresh from the snapshot so the
  // card shows confirmed state rather than what we hoped for.
  await refreshDevices();
}

/* --- Rendering ------------------------------------------------------------ */

function groupByRoom(devices) {
  const groups = new Map();
  for (const device of devices) {
    const key = device.roomName ?? t("home.noRoom");
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(device);
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b, "de"));
}

function renderHome() {
  const devices = [...state.devices.values()];
  dom.homeEmpty.hidden = devices.length > 0;
  dom.rooms.replaceChildren(
    ...groupByRoom(devices).map(([room, roomDevices]) => renderRoom(room, roomDevices, ctx)),
  );
}

function patchDevice(deviceId, { force = false } = {}) {
  if (!force && state.held.has(deviceId)) return;
  const device = state.devices.get(deviceId);
  const existing = dom.rooms.querySelector(`[data-device-id="${CSS.escape(deviceId)}"]`);
  if (!device || !existing) {
    renderHome();
    return;
  }
  existing.replaceWith(renderDevice(device, ctx));
}

async function refreshDevices() {
  const devices = await state.api.devices();
  const previous = state.devices.size;
  state.devices = new Map(devices.map((device) => [device.id, device]));
  if (previous !== state.devices.size) renderHome();
  else for (const device of devices) patchDevice(device.id);
  dom.settingsDevices.textContent = String(state.devices.size);
}

async function refreshActivity() {
  const entries = await state.api.activity(50);
  dom.activityEmpty.hidden = entries.length > 0;
  dom.activityList.replaceChildren(...entries.map(renderActivityEntry));
}

function setView(view) {
  state.view = view;
  for (const [name, node] of Object.entries(dom.views)) node.hidden = name !== view;
  for (const tab of dom.tabs) tab.setAttribute("aria-selected", String(tab.dataset.view === view));
  dom.title.textContent = t(`title.${view}`);
  if (view === "activity") refreshActivity().catch(reportError);
}

function setLinkState(status) {
  dom.link.dataset.state = status;
  dom.linkLabel.textContent = t(`link.${status}`);
}

let toastTimer = null;
function toast(message, kind = "info") {
  dom.toast.textContent = message;
  dom.toast.className = kind === "error" ? "toast toast--error" : "toast";
  dom.toast.hidden = false;
  if (toastTimer) window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    dom.toast.hidden = true;
  }, TOAST_MS);
}

function reportError(error) {
  if (error instanceof ApiError && error.type === "unauthenticated") {
    signOut();
    return;
  }
  toast(error instanceof ApiError ? error.localizedMessage : t("error.network"), "error");
}

/* --- Session -------------------------------------------------------------- */

async function startSession(token) {
  const api = new Api(token);
  const identity = await api.me();

  state.api = api;
  state.identity = identity;

  dom.settingsClient.textContent = identity.displayName;
  dom.settingsMode.textContent = identity.demoMode ? t("mode.demo") : t("mode.live");
  dom.settingsHost.textContent = window.location.host;

  await refreshDevices();

  state.live = new LiveConnection(api, {
    onStatus: setLinkState,
    onResync: () => refreshDevices().catch(reportError),
    onState: (device) => {
      state.devices.set(device.id, device);
      patchDevice(device.id);
      if (state.view === "activity") refreshActivity().catch(() => {});
    },
  });
  state.live.start();

  dom.connect.hidden = true;
  dom.shell.hidden = false;
  setView("home");
}

function signOut() {
  state.live?.stop();
  state.live = null;
  state.api = null;
  state.devices.clear();
  state.notices.clear();
  state.busy.clear();
  clearCredential();
  dom.shell.hidden = true;
  dom.connect.hidden = false;
  dom.connectToken.value = "";
  dom.connectError.hidden = true;
}

async function attemptConnect(token, { remember }) {
  dom.connectError.hidden = true;
  dom.connectSubmit.disabled = true;
  try {
    await startSession(token);
    if (remember) storeCredential(token);
    return true;
  } catch (error) {
    const message = error instanceof ApiError ? error.localizedMessage : t("error.network");
    dom.connectError.textContent = message;
    dom.connectError.hidden = false;
    dom.connect.hidden = false;
    dom.shell.hidden = true;
    return false;
  } finally {
    dom.connectSubmit.disabled = false;
  }
}

/* --- Wiring --------------------------------------------------------------- */

function wire() {
  dom.connectForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const token = dom.connectToken.value.trim();
    if (token) attemptConnect(token, { remember: true });
  });

  for (const tab of dom.tabs) {
    tab.addEventListener("click", () => setView(tab.dataset.view));
  }

  dom.signOut.addEventListener("click", signOut);

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") state.live?.wake();
  });
  window.addEventListener("online", () => state.live?.wake());
}

async function boot() {
  applyStaticStrings();
  wire();
  setLinkState("offline");

  const saved = loadCredential();
  if (saved) {
    const connected = await attemptConnect(saved, { remember: false });
    if (!connected) clearCredential();
  } else {
    dom.connect.hidden = false;
  }

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => {
      /* The application works without offline shell caching. */
    });
  }
}

boot().catch(() => {
  dom.connect.hidden = false;
});
