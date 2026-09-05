/* Device rendering.

   Controls are built from the capabilities the gateway reports, so a control
   that a device cannot actually perform never appears; see
   docs/adr/0008-canonical-capability-model.md. Limits such as the pool
   temperature range come from the device's own constraints, not from a
   constant in here.

   All device text goes through textContent. Device names originate from vendor
   adapters and are treated as untrusted input. */

import { temperatureDial } from "./dial.js";
import { el, svg } from "./dom.js";
import { t } from "./strings.js";

export { el };

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

/** A moment as a person would say it: today, yesterday, or a weekday. */
export function formatMoment(isoString, now = new Date()) {
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return "—";

  const time = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" }).format(date);
  const midnight = (value) => new Date(value.getFullYear(), value.getMonth(), value.getDate());
  const days = Math.round((midnight(now) - midnight(date)) / 86400000);

  if (days === 0) return t("time.today", { time });
  if (days === 1) return t("time.yesterday", { time });
  if (days > 1 && days < 7) {
    const weekday = new Intl.DateTimeFormat("de-DE", { weekday: "long" }).format(date);
    return t("time.weekday", { weekday, time });
  }
  const day = new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "2-digit" }).format(date);
  return t("time.date", { date: day, time });
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

/* --- Pool ---------------------------------------------------------------- */

/* Function icons. Line art only, so one stroke colour follows the tile state. */
const ICONS = {
  heater: "M12 3c2.6 3 1.2 4.7.4 5.9-.9 1.4-.3 3 1.2 3.4 1.3.3 2.2-.6 2.3-1.8 1.6 1.7 2.1 3.4 2.1 5 0 3.3-2.7 5.5-6 5.5s-6-2.2-6-5.5C6 11.4 9.6 9.1 12 3Z",
  filter: "M20 12a8 8 0 0 1-13.7 5.6M4 12a8 8 0 0 1 13.7-5.6M6.3 17.6H4.4v1.9m13.3-13v1.9h1.9",
  bubbles: "M8 16.5a3 3 0 1 1 0-6 3 3 0 0 1 0 6Zm8.5-2a2.2 2.2 0 1 1 0-4.4 2.2 2.2 0 0 1 0 4.4ZM13 8.2a1.6 1.6 0 1 1 0-3.2 1.6 1.6 0 0 1 0 3.2Z",
  lock: "M7 10.5V8a5 5 0 0 1 10 0v2.5M5.8 10.5h12.4a1 1 0 0 1 1 1V19a1 1 0 0 1-1 1H5.8a1 1 0 0 1-1-1v-7.5a1 1 0 0 1 1-1Z",
  clock: "M12 20.5a8.5 8.5 0 1 1 0-17 8.5 8.5 0 0 1 0 17ZM12 7.2V12l3.2 2",
  thermometer:
    "M14 13.4V6a2 2 0 1 0-4 0v7.4a4 4 0 1 0 4 0ZM12 11v5.4M16.8 7.5h2.6M16.8 10.6h2.6",
};

function icon(name, className = "tile__icon") {
  return svg("svg", { class: className, viewBox: "0 0 24 24", "aria-hidden": "true" }, [
    svg("path", {
      d: ICONS[name],
      fill: "none",
      "stroke-width": "1.6",
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
    }),
  ]);
}

/** One function of the tub: on, off, and whether it can be changed here.
 *
 * A press is not blocked while an earlier one is still travelling to the
 * controller. These are desired-state commands, so the newest press simply
 * replaces the one in flight -- waiting five seconds to be allowed to undo
 * something is not a safety property, it is just a wait.
 *
 * While a press is unconfirmed the tile shows what was asked for, marked as
 * pending. If the controller refuses, the confirmed state comes back and the
 * tile returns to it with a message; nothing is ever shown as done that the
 * device did not do.
 */
function functionTile({ name, label, tone, value, pending, offline, onToggle }) {
  const shown = pending === null || pending === undefined ? value : pending;
  const stateText = shown ? t("state.on") : t("state.off");
  const classes = ["tile"];
  if (shown) classes.push("tile--on");
  if (pending !== null && pending !== undefined) classes.push("tile--pending");

  if (!onToggle) {
    // The gateway knows this value; it just has not released the control.
    // Hiding what is known would be a different kind of dishonesty than
    // offering a control that is not proven.
    return el(
      "div",
      {
        class: `${classes.join(" ")} tile--static`,
        dataset: { tone },
        role: "img",
        "aria-label": `${label}: ${stateText}`,
      },
      [
        icon(name),
        el("span", { class: "tile__label", text: label }),
        el("span", { class: "tile__state", text: stateText }),
      ],
    );
  }

  const tile = el("button", {
    class: classes.join(" "),
    type: "button",
    dataset: { tone },
    "aria-pressed": String(Boolean(shown)),
    "aria-label": label,
    onclick: () => onToggle(!shown),
  });
  if (offline) tile.disabled = true;
  tile.append(
    icon(name),
    el("span", { class: "tile__label", text: label }),
    el("span", { class: "tile__switch" }, [el("span", { class: "tile__knob" })]),
  );
  return tile;
}

/* --- Timers -------------------------------------------------------------- */

/** What a timer may be set to. Whole hours mostly, with one short option. */
const TIMER_HOURS = [0.5, 1, 1.5, 2, 3, 4, 6, 8, 12, 24];

/** The functions that may be put on a timer; the gateway enforces the same. */
const TIMED = [
  ["HEATING", "pool.heater", "SET_HEATER"],
  ["FILTER", "pool.filter", "SET_FILTER"],
];

function formatHours(hours) {
  if (hours < 1) return t("time.minutes", { minutes: Math.round(hours * 60) });
  return t("time.hours", { hours: formatNumber(hours, hours % 1 === 0 ? 0 : 1) });
}

/** How long is left, or null once a timer is due. */
function remaining(dueAtIso, now = Date.now()) {
  const seconds = (new Date(dueAtIso).getTime() - now) / 1000;
  return Number.isFinite(seconds) && seconds > 0 ? seconds : null;
}

function armedTimerRow(device, label, timer, ctx, busy) {
  const left = remaining(timer.dueAt);
  const phrase = timer.desired ? "timer.startsIn" : "timer.stopsIn";
  const cancel = el("button", {
    class: "button button--quiet",
    type: "button",
    text: t("timer.cancel"),
    onclick: () => ctx.cancelTimer(device, timer),
  });
  if (busy) cancel.disabled = true;

  return el("div", { class: "timer__row timer__row--armed" }, [
    el("div", { class: "timer__text" }, [
      el("span", { class: "timer__label", text: label }),
      el("span", {
        class: "timer__state",
        text: left === null ? t("timer.due") : t(phrase, { value: formatDuration(left) }),
      }),
    ]),
    cancel,
  ]);
}

function timerPickerRow(device, label, action, ctx, disabled) {
  const select = el("select", { class: "timer__hours", "aria-label": t("timer.hours") });
  for (const hours of TIMER_HOURS) {
    select.append(el("option", { value: String(hours), text: formatHours(hours) }));
  }
  select.value = "3";
  if (disabled) select.disabled = true;

  const action_button = (labelKey, kind) => {
    const button = el("button", {
      class: "button button--quiet",
      type: "button",
      text: t(labelKey),
      onclick: () => ctx.createTimer(device, action, kind, Number(select.value)),
    });
    if (disabled) button.disabled = true;
    return button;
  };

  return el("div", { class: "timer__row" }, [
    el("span", { class: "timer__label", text: label }),
    el("div", { class: "timer__pick" }, [
      select,
      action_button("timer.startIn", "DELAYED_START"),
      action_button("timer.runFor", "RUN_FOR"),
    ]),
  ]);
}

/** The one place in HomeFlow that arranges a write nobody will be watching. */
function timerSection(device, ctx) {
  const offline = device.availability === "OFFLINE";
  const armed = ctx.schedulesFor(device.id);
  const rows = [];

  for (const [capability, labelKey, action] of TIMED) {
    if (!has(device, capability)) continue;
    const busy = ctx.isBusy(device.id, `TIMER_${action}`);
    const timer = armed.find((item) => item.action === action);
    rows.push(
      timer
        ? armedTimerRow(device, t(labelKey), timer, ctx, busy)
        : timerPickerRow(device, t(labelKey), action, ctx, offline || busy),
    );
  }

  if (!rows.length) return null;
  return el("div", { class: "timer" }, [
    el("h3", { class: "timer__title", text: t("timer.title") }),
    ...rows,
    el("p", { class: "timer__note", text: t("timer.note") }),
  ]);
}

/** A single labelled fact under the controls. */
function factCard(art, label, value, hint) {
  return el("div", { class: "fact" }, [
    icon(art, "fact__icon"),
    el("span", { class: "fact__label", text: label }),
    el("span", { class: "fact__value", text: value }),
    hint ? el("span", { class: "fact__hint", text: hint }) : null,
  ]);
}

function poolBody(device, ctx) {
  const { state, constraints } = device;
  const offline = device.availability === "OFFLINE";
  const parts = [];

  const min = constraints.targetTemperatureMinC;
  const max = constraints.targetTemperatureMaxC;
  const target = state.targetTemperatureC;
  const reading = state.currentTemperatureC ?? null;

  if (has(device, "TARGET_TEMPERATURE") && min !== null && max !== null && target != null) {
    parts.push(
      temperatureDial({
        value: target,
        reading,
        min,
        max,
        step: constraints.targetTemperatureStepC ?? 0.5,
        label: t("pool.targetLabel"),
        readingLabel: t("pool.current"),
        targetLabel: t("pool.targetShort"),
        // A setpoint is a value, not an act: the newest one replaces the one
        // still in flight, so the dial never waits for the controller.
        disabled: offline,
        format: (value) => formatNumber(value),
        onDrag: () => ctx.holdRender(device.id),
        onCommit: (celsius) =>
          ctx.execute(device, "SET_TARGET_TEMPERATURE", { celsius: Number(celsius) }),
      }),
    );
  } else if (reading !== null) {
    parts.push(
      el("div", { class: "readout" }, [
        el("span", { class: "readout__value", text: formatNumber(reading) }),
        el("span", { class: "readout__unit", text: "°C" }),
        target == null
          ? null
          : el("span", {
              class: "readout__target",
              text: t("pool.target", { value: formatNumber(target) }),
            }),
      ]),
    );
  }

  const functions = [
    ["HEATING", "heater", "warm", "pool.heater", "SET_HEATER", state.heater],
    ["FILTER", "filter", "cool", "pool.filter", "SET_FILTER", state.filterPump],
    ["BUBBLES", "bubbles", "cool", "pool.bubbles", "SET_BUBBLES", state.bubbles],
    [
      "CONTROL_PANEL_LOCK",
      "lock",
      "neutral",
      "pool.panelLock",
      "SET_CONTROL_PANEL_LOCK",
      state.controlPanelLock,
    ],
  ];

  const tiles = [];
  let readOnly = 0;
  for (const [capability, art, tone, labelKey, action, value] of functions) {
    if (value === null || value === undefined) continue;
    const released = has(device, capability);
    if (!released) readOnly += 1;
    tiles.push(
      functionTile({
        name: art,
        label: t(labelKey),
        tone,
        value,
        pending: ctx.desiredFor(device.id, action),
        offline,
        onToggle: released ? (on) => ctx.execute(device, action, { on }) : null,
      }),
    );
  }
  if (tiles.length) parts.push(el("div", { class: "tiles" }, tiles));

  const facts = [];
  if (state.filterPump !== null && state.filterPump !== undefined) {
    facts.push(
      state.filterLastStartedAt
        ? factCard(
            "clock",
            t("pool.lastFiltered"),
            formatMoment(state.filterLastStartedAt),
            state.filterPump ? t("pool.filteringNow") : null,
          )
        : // Nothing is invented: a pump already running when the gateway
          // started has no start time we witnessed.
          factCard("clock", t("pool.lastFiltered"), t("pool.lastFilteredUnknown"), null),
    );
  }
  if (facts.length) parts.push(el("div", { class: "facts-grid" }, facts));

  // One wrapper so the dial can sit beside the functions on a wide screen
  // without the timers and notices below it joining that arrangement.
  return [
    el("div", { class: "pool" }, parts),
    timerSection(device, ctx),
    readOnly > 0 ? el("p", { class: "device__message", text: t("device.readOnly") }) : null,
  ].filter(Boolean);
}

/** A device that only reports a number: the outdoor thermometer, for now. */
function sensorBody(device) {
  const reading = device.state.currentTemperatureC;
  if (reading === null || reading === undefined) return [];
  return [
    el("div", { class: "readout readout--compact" }, [
      icon("thermometer", "readout__icon"),
      el("span", { class: "readout__value", text: formatNumber(reading) }),
      el("span", { class: "readout__unit", text: "°C" }),
    ]),
  ];
}

function statusRow(label, value) {
  // A value the gateway reports but has not released for control. The pool
  // card learned this the hard way: hiding what is known is its own kind of
  // dishonesty, different from offering a control that is not proven.
  return el("div", { class: "control" }, [
    el("span", { class: "control__label", text: label }),
    el("span", { class: "control__value", text: value }),
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

function lightBody(device, ctx) {
  const { state } = device;
  const offline = device.availability === "OFFLINE";
  const controls = [];
  let readOnly = 0;

  if (state.power !== null && state.power !== undefined) {
    if (has(device, "POWER")) {
      controls.push(
        toggleRow(t("device.power"), {
          checked: ctx.desiredFor(device.id, "SET_POWER") ?? state.power,
          disabled: offline,
          onToggle: (on) => ctx.execute(device, "SET_POWER", { on }),
        }),
      );
    } else {
      readOnly += 1;
      controls.push(statusRow(t("device.power"), state.power ? t("state.on") : t("state.off")));
    }
  }

  if (state.brightness !== null && state.brightness !== undefined) {
    if (has(device, "BRIGHTNESS")) {
      controls.push(
        sliderRow(t("device.brightness"), {
          value: state.brightness,
          min: 0,
          max: 100,
          step: 1,
          unit: " %",
          disabled: offline,
          onDrag: () => ctx.holdRender(device.id),
          onCommit: (brightness) => ctx.execute(device, "SET_BRIGHTNESS", { brightness }),
        }),
      );
    } else {
      readOnly += 1;
      controls.push(statusRow(t("device.brightness"), `${state.brightness} %`));
    }
  }

  return [
    controls.length ? el("div", { class: "controls" }, controls) : null,
    readOnly > 0 ? el("p", { class: "device__message", text: t("device.readOnly") }) : null,
  ].filter(Boolean);
}

/** A room thermostat: what it measures, and what it is aiming for. */
function thermostatBody(device, ctx) {
  const { state, constraints } = device;
  const offline = device.availability === "OFFLINE";
  const parts = [];

  if (state.currentTemperatureC !== null && state.currentTemperatureC !== undefined) {
    parts.push(
      el("div", { class: "readout" }, [
        el("span", { class: "readout__value", text: formatNumber(state.currentTemperatureC) }),
        el("span", { class: "readout__unit", text: "°C" }),
        state.heater ? el("span", { class: "readout__target", text: t("climate.heating") }) : null,
      ]),
    );
  }

  const controls = [];
  let readOnly = 0;
  const target = state.targetTemperatureC;
  const min = constraints.targetTemperatureMinC;
  const max = constraints.targetTemperatureMaxC;

  if (target !== null && target !== undefined) {
    if (has(device, "TARGET_TEMPERATURE") && min !== null && max !== null) {
      controls.push(
        sliderRow(t("climate.target"), {
          value: target,
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
    } else {
      readOnly += 1;
      controls.push(statusRow(t("climate.target"), `${formatNumber(target)} °C`));
    }
  }

  if (controls.length) parts.push(el("div", { class: "controls" }, controls));
  if (readOnly > 0) {
    parts.push(el("p", { class: "device__message", text: t("device.readOnly") }));
  }
  return parts;
}

function mediaBody(device, ctx) {
  const offline = device.availability === "OFFLINE";
  const busy = (action) => offline || ctx.isBusy(device.id, action);
  const { state } = device;
  const parts = [];
  const controls = [];

  let readOnly = 0;

  if (state.playback) {
    if (has(device, "MEDIA_PLAYBACK")) {
      const playing = state.playback === "PLAYING";
      const button = el("button", {
        class: "button button--quiet",
        type: "button",
        text: playing ? t("device.pause") : t("device.play"),
        onclick: () => ctx.execute(device, "SET_PLAYBACK", { playback: playing ? "PAUSE" : "PLAY" }),
      });
      if (busy("SET_PLAYBACK")) button.disabled = true;
      controls.push(
        el("div", { class: "control" }, [
          el("span", { class: "control__label", text: t(`playback.${state.playback}`) }),
          button,
        ]),
      );
    } else {
      readOnly += 1;
      controls.push(statusRow(t("device.playback"), t(`playback.${state.playback}`)));
    }
  }

  if (state.programName) {
    parts.push(el("p", { class: "device__meta", text: state.programName }));
  }

  if (state.volume !== null && state.volume !== undefined) {
    if (has(device, "VOLUME")) {
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
    } else {
      readOnly += 1;
      controls.push(statusRow(t("device.volume"), `${state.volume} %`));
    }
  }

  if (controls.length) parts.push(el("div", { class: "controls" }, controls));
  if (readOnly > 0) {
    parts.push(el("p", { class: "device__message", text: t("device.readOnly") }));
  }
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
  SENSOR: sensorBody,
  THERMOSTAT: thermostatBody,
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

  // The kind decides how much room the card gets in the grid; a hot tub is
  // what someone opens this for, an outdoor thermometer is a glance.
  const kindClass = `device--${device.kind.toLowerCase().replaceAll("_", "-")}`;
  const card = el("article", {
    class: `card device ${kindClass}${offline ? " device--unreachable" : ""}`,
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
