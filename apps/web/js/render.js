/* Device rendering.

   Controls are built from the capabilities the gateway reports, so a control
   that a device cannot actually perform never appears; see
   docs/adr/0008-canonical-capability-model.md. Limits such as the pool
   temperature range come from the device's own constraints, not from a
   constant in here.

   All device text goes through textContent. Device names originate from vendor
   adapters and are treated as untrusted input. */

import { t } from "./strings.js";

/** Small DOM builder. */
export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2).toLowerCase(), value);
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child) node.append(child);
  }
  return node;
}

function has(device, capability) {
  return device.capabilities.includes(capability);
}

function formatNumber(value, digits = 1) {
  return new Intl.NumberFormat("de-DE", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

function formatClock(isoString) {
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" }).format(date);
}

export function formatDuration(totalSeconds) {
  const minutes = Math.round(totalSeconds / 60);
  if (minutes >= 60) {
    return t("time.hoursMinutes", { hours: Math.floor(minutes / 60), minutes: minutes % 60 });
  }
  return t("time.minutes", { minutes });
}

/* --- Controls ------------------------------------------------------------ */

function toggleRow(label, { checked, disabled, onToggle }) {
  const button = el("button", {
    class: "switch",
    type: "button",
    role: "switch",
    "aria-checked": String(Boolean(checked)),
    "aria-label": label,
  });
  if (disabled) button.disabled = true;
  button.addEventListener("click", () => onToggle(!checked));
  return el("div", { class: "control" }, [el("span", { class: "control__label", text: label }), button]);
}

function statusRow(label, value) {
  // A value the gateway reports but the client may not change yet.
  return el("div", { class: "control" }, [
    el("span", { class: "control__label", text: label }),
    el("span", {
      class: "control__value",
      text: value ? t("state.on") : t("state.off"),
    }),
  ]);
}


function sliderRow(label, { value, min, max, step, unit, disabled, format, onCommit, onDrag }) {
  const readout = el("span", {
    class: "control__value",
    text: `${format ? format(value) : value}${unit ?? ""}`,
  });

  const input = el("input", {
    class: "slider",
    type: "range",
    min: String(min),
    max: String(max),
    step: String(step),
    value: String(value),
    "aria-label": label,
  });
  if (disabled) input.disabled = true;

  input.addEventListener("input", () => {
    const next = Number(input.value);
    readout.textContent = `${format ? format(next) : next}${unit ?? ""}`;
    onDrag?.();
  });
  input.addEventListener("change", () => onCommit(Number(input.value)));

  return el("div", { class: "control control--stacked" }, [
    el("div", { class: "control__head" }, [
      el("span", { class: "control__label", text: label }),
      readout,
    ]),
    input,
  ]);
}

/* --- Device bodies -------------------------------------------------------- */

function poolBody(device, ctx) {
  const { state, constraints } = device;
  const offline = device.availability === "OFFLINE";
  // Only the control being operated waits. A pending heater command must not
  // take the pump switch away, which is exactly when it is wanted.
  const busy = (action) => offline || ctx.isBusy(device.id, action);
  // A setpoint is a value, not an act: the newest one simply replaces the one
  // still in flight, so the slider never has to wait for the controller.
  const parts = [];

  if (state.currentTemperatureC !== null && state.currentTemperatureC !== undefined) {
    parts.push(
      el("div", { class: "readout" }, [
        el("span", { class: "readout__value", text: formatNumber(state.currentTemperatureC) }),
        el("span", { class: "readout__unit", text: "°C" }),
        state.targetTemperatureC === null || state.targetTemperatureC === undefined
          ? null
          : el("span", {
              class: "readout__target",
              text: t("pool.target", { value: formatNumber(state.targetTemperatureC) }),
            }),
      ]),
    );
  }

  const controls = [];

  const min = constraints.targetTemperatureMinC;
  const max = constraints.targetTemperatureMaxC;
  if (
    has(device, "TARGET_TEMPERATURE") &&
    min !== null &&
    max !== null &&
    state.targetTemperatureC !== null &&
    state.targetTemperatureC !== undefined
  ) {
    controls.push(
      sliderRow(t("pool.targetLabel"), {
        value: state.targetTemperatureC,
        min,
        max,
        step: constraints.targetTemperatureStepC ?? 0.5,
        unit: " °C",
        format: (value) => formatNumber(value),
        disabled: offline,
        onDrag: () => ctx.holdRender(device.id),
        onCommit: (celsius) =>
          ctx.execute(device, "SET_TARGET_TEMPERATURE", { celsius: Number(celsius) }),
      }),
    );
  }

  const switches = [
    ["HEATING", "pool.heater", "SET_HEATER", state.heater],
    ["FILTER", "pool.filter", "SET_FILTER", state.filterPump],
    ["BUBBLES", "pool.bubbles", "SET_BUBBLES", state.bubbles],
    ["CONTROL_PANEL_LOCK", "pool.panelLock", "SET_CONTROL_PANEL_LOCK", state.controlPanelLock],
  ];
  let readOnlyRows = 0;
  for (const [capability, labelKey, action, value] of switches) {
    if (value === null || value === undefined) continue;
    if (has(device, capability)) {
      controls.push(
        toggleRow(t(labelKey), {
          checked: value,
          disabled: busy(action),
          onToggle: (on) => ctx.execute(device, action, { on }),
        }),
      );
    } else {
      // The gateway knows this value; it just has not released the control.
      // Hiding what is known would be a different kind of dishonesty than
      // offering a control that is not proven.
      readOnlyRows += 1;
      controls.push(statusRow(t(labelKey), value));
    }
  }

  if (controls.length) parts.push(el("div", { class: "controls" }, controls));
  if (readOnlyRows > 0) {
    parts.push(el("p", { class: "device__message", text: t("device.readOnly") }));
  }
  return parts;
}

function lightBody(device, ctx) {
  const offline = device.availability === "OFFLINE";
  const busy = (action) => offline || ctx.isBusy(device.id, action);
  const controls = [];

  if (has(device, "POWER") && device.state.power !== null && device.state.power !== undefined) {
    controls.push(
      toggleRow(t("device.power"), {
        checked: device.state.power,
        disabled: busy("SET_POWER"),
        onToggle: (on) => ctx.execute(device, "SET_POWER", { on }),
      }),
    );
  }

  if (
    has(device, "BRIGHTNESS") &&
    device.state.brightness !== null &&
    device.state.brightness !== undefined
  ) {
    controls.push(
      sliderRow(t("device.brightness"), {
        value: device.state.brightness,
        min: 0,
        max: 100,
        step: 1,
        unit: " %",
        disabled: offline,
        onDrag: () => ctx.holdRender(device.id),
        onCommit: (brightness) => ctx.execute(device, "SET_BRIGHTNESS", { brightness }),
      }),
    );
  }

  return controls.length ? [el("div", { class: "controls" }, controls)] : [];
}

function mediaBody(device, ctx) {
  const offline = device.availability === "OFFLINE";
  const busy = (action) => offline || ctx.isBusy(device.id, action);
  const { state } = device;
  const parts = [];
  const controls = [];

  if (has(device, "MEDIA_PLAYBACK") && state.playback) {
    const playing = state.playback === "PLAYING";
    const button = el("button", {
      class: "button button--quiet",
      type: "button",
      text: playing ? t("device.pause") : t("device.play"),
      onclick: () =>
        ctx.execute(device, "SET_PLAYBACK", { playback: playing ? "PAUSE" : "PLAY" }),
    });
    if (busy("SET_PLAYBACK")) button.disabled = true;
    controls.push(
      el("div", { class: "control" }, [
        el("span", { class: "control__label", text: t(`playback.${state.playback}`) }),
        button,
      ]),
    );
  }

  if (has(device, "VOLUME") && state.volume !== null && state.volume !== undefined) {
    controls.push(
      sliderRow(t("device.volume"), {
        value: state.volume,
        min: 0,
        max: 100,
        step: 1,
        unit: " %",
        disabled: offline,
        onDrag: () => ctx.holdRender(device.id),
        onCommit: (volume) => ctx.execute(device, "SET_VOLUME", { volume }),
      }),
    );
  }

  if (controls.length) parts.push(el("div", { class: "controls" }, controls));
  return parts;
}

function lockBody(device, ctx) {
  const disabled =
    device.availability === "OFFLINE" || ctx.isBusy(device.id, "SET_LOCK_STATE");
  const lockState = device.state.lockState ?? "UNKNOWN";
  const parts = [
    el("div", { class: "readout" }, [
      el("span", { class: "readout__value", text: t(`lock.${lockState}`) }),
    ]),
  ];

  const actions = [];
  if (has(device, "LOCK")) {
    const button = el("button", {
      class: "button button--quiet",
      type: "button",
      text: t("lock.lock"),
      onclick: () => ctx.execute(device, "SET_LOCK_STATE", { desired: "LOCKED" }),
    });
    if (disabled || lockState === "LOCKED") button.disabled = true;
    actions.push(button);
  }
  if (has(device, "UNLOCK")) {
    // The gateway refuses this until fresh device-owner authorisation exists.
    // Offering an enabled button that always fails would be a lie.
    const button = el("button", {
      class: "button button--quiet",
      type: "button",
      text: t("lock.unlock"),
    });
    button.disabled = true;
    actions.push(button);
  }
  if (actions.length) parts.push(el("div", { class: "actions" }, actions));

  if (has(device, "UNLOCK")) {
    parts.push(
      el("p", {
        class: "device__message",
        text: t("error.action_authorization_required"),
      }),
    );
  }
  return parts;
}

function applianceBody(device) {
  const { state } = device;
  const parts = [
    el("div", { class: "readout" }, [
      el("span", {
        class: "readout__value",
        text: t(`program.${state.program ?? "UNKNOWN"}`),
      }),
    ]),
  ];

  const details = [];
  if (state.programName) details.push(state.programName);
  if (state.remainingSeconds) {
    details.push(t("program.remaining", { value: formatDuration(state.remainingSeconds) }));
  }
  if (details.length) {
    parts.push(el("p", { class: "device__meta", text: details.join(" · ") }));
  }
  return parts;
}

const BODIES = {
  POOL: poolBody,
  LIGHT: lightBody,
  SWITCH: lightBody,
  MEDIA_PLAYER: mediaBody,
  LOCK: lockBody,
  WASHING_MACHINE: applianceBody,
  DISHWASHER: applianceBody,
};

/* --- Card ---------------------------------------------------------------- */

export function renderDevice(device, ctx) {
  const offline = device.availability === "OFFLINE";
  const badges = [];
  if (offline) badges.push(el("span", { class: "badge badge--danger", text: t("badge.offline") }));
  else if (device.isStale) {
    badges.push(el("span", { class: "badge badge--warn", text: t("badge.stale") }));
  }

  const card = el("article", {
    class: `card device${offline ? " device--unreachable" : ""}`,
    dataset: { deviceId: device.id },
  });

  card.append(
    el("div", { class: "device__head" }, [
      el("div", {}, [
        el("div", { class: "device__name", text: device.displayName }),
        el("div", {
          class: "device__meta",
          text: t("device.observed", { time: formatClock(device.stateObservedAt) }),
        }),
      ]),
      badges.length ? el("div", { class: "badges" }, badges) : null,
    ]),
  );

  const body = BODIES[device.kind];
  if (body) {
    for (const part of body(device, ctx)) card.append(part);
  }

  const notice = ctx.noticeFor(device.id);
  if (notice) {
    card.append(
      el("p", {
        class: `device__message device__message--${notice.kind}`,
        text: notice.text,
      }),
    );
  }

  if (ctx.hasPending(device.id)) card.setAttribute("aria-busy", "true");
  return card;
}

export function renderRoom(name, devices, ctx) {
  return el("section", { class: "room" }, [
    el("h2", { class: "room__name", text: name }),
    el(
      "div",
      { class: "room__devices" },
      devices.map((device) => renderDevice(device, ctx)),
    ),
  ]);
}

export function renderActivityEntry(entry) {
  const label = t(`activity.${entry.event}`);
  const detail = [entry.action, entry.outcome, entry.riskClass].filter(Boolean).join(" · ");
  return el("li", { class: "activity__item" }, [
    el("div", { class: "activity__body" }, [
      el("div", { class: "activity__event", text: label === `activity.${entry.event}` ? entry.event : label }),
      detail ? el("div", { class: "activity__detail", text: detail }) : null,
    ]),
    el("time", { class: "activity__time", text: formatClock(entry.occurredAt) }),
  ]);
}
