/* What the pool card renders, checked without a browser.

   The rule this guards: a control appears only for a capability the gateway
   released, but a value the gateway reports is always shown. Conflating the two
   once hid the heater, filter and bubble state of a working controller. */

import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const renderPath = path.resolve(here, "../../../apps/web/js/render.js");

/* A DOM small enough to render into and read back. */
function makeNode(tag) {
  return {
    tag,
    className: "",
    textContent: "",
    dataset: {},
    attributes: {},
    disabled: false,
    children: [],
    addEventListener() {},
    setAttribute(name, value) {
      this.attributes[name] = value;
      if (name === "class") this.className = value;
    },
    append(...nodes) {
      this.children.push(...nodes);
    },
  };
}

globalThis.document = {
  createElement: makeNode,
  // The dial draws itself in SVG, which lives in its own namespace.
  createElementNS: (_ns, tag) => makeNode(tag),
};

function flatten(node, out = []) {
  out.push(node);
  for (const child of node.children) flatten(child, out);
  return out;
}

function text(node) {
  return flatten(node)
    .map((item) => item.textContent)
    .filter(Boolean)
    .join(" | ");
}

const classed = (node, name) =>
  flatten(node).filter((item) => item.className.split(" ").includes(name));

/* A tile the user can press, as opposed to one that only reports a value. */
const controls = (node) => classed(node, "tile").filter((item) => item.tag === "button");

const { renderDevice } = await import(pathToFileURL(renderPath).href);

function context(pending = new Set(), schedules = [], desired = new Map()) {
  return {
    isBusy: (deviceId, action) => pending.has(`${deviceId}|${action}`),
    hasPending: (deviceId) => [...pending].some((key) => key.startsWith(`${deviceId}|`)),
    noticeFor: () => null,
    holdRender: () => {},
    execute: () => {},
    desiredFor: (deviceId, action) => desired.get(`${deviceId}|${action}`) ?? null,
    schedulesFor: () => schedules,
    createTimer: () => {},
    cancelTimer: () => {},
  };
}

const ctx = context();

function pool(capabilities, state = {}) {
  return {
    id: "00000000-0000-4000-8000-000000000000",
    displayName: "Pool",
    kind: "POOL",
    roomName: "Terrace",
    capabilities,
    availability: "ONLINE",
    isStale: false,
    stateObservedAt: "2026-09-01T17:00:00+00:00",
    constraints: {
      targetTemperatureMinC: 20,
      targetTemperatureMaxC: 40,
      targetTemperatureStepC: 1,
    },
    state: {
      currentTemperatureC: 27,
      targetTemperatureC: 36,
      heater: true,
      filterPump: true,
      bubbles: false,
      controlPanelLock: false,
      ...state,
    },
  };
}

const ALL = [
  "CURRENT_TEMPERATURE",
  "TARGET_TEMPERATURE",
  "HEATING",
  "FILTER",
  "BUBBLES",
  "CONTROL_PANEL_LOCK",
];

/* Read-only: every value is visible, no control is offered. */
{
  const card = renderDevice(pool(["CURRENT_TEMPERATURE"]), ctx);
  const shown = text(card);

  assert.equal(controls(card).length, 0, "no control may appear for an unreleased capability");
  for (const label of ["Heizung", "Filterpumpe", "Massagedüsen", "Bedienfeldsperre"]) {
    assert.ok(shown.includes(label), `${label} must be shown even without a control`);
  }
  assert.ok(shown.includes("An"), "an active function must read as on");
  assert.ok(shown.includes("Aus"), "an inactive function must read as off");
  assert.ok(shown.includes("27"), "the water temperature must be shown");
  assert.ok(shown.includes("Nur Anzeige"), "the reason for the missing controls must be stated");
  assert.equal(classed(card, "dial").length, 0, "no setpoint dial without the capability");
}

/* Released: the released capability becomes a control, the rest stay read-only. */
{
  const card = renderDevice(pool(["CURRENT_TEMPERATURE", "BUBBLES"]), ctx);
  const shown = text(card);

  assert.equal(controls(card).length, 1, "exactly the released capability gets a control");
  assert.ok(shown.includes("Heizung"), "unreleased values stay visible");
  assert.ok(shown.includes("Nur Anzeige"), "the note stays while anything is read-only");
}

/* Everything released: controls only, and no note. */
{
  const card = renderDevice(pool(ALL), ctx);

  assert.equal(controls(card).length, 4, "every released capability gets a control");
  assert.ok(!text(card).includes("Nur Anzeige"), "the note goes away when nothing is read-only");
}

console.log("pool card: read-only state visible, controls only when released");

/* A command in flight never takes a control away. These are desired-state
   commands, so the next press replaces the one travelling rather than queueing
   behind it -- waiting to be allowed to undo something is not a safety
   property. What is in flight is shown as pending, never as done. */
{
  const device = pool(ALL, { heater: false });
  const inFlight = context(
    new Set([`${device.id}|SET_HEATER`]),
    [],
    new Map([[`${device.id}|SET_HEATER`, true]]),
  );
  const card = renderDevice(device, inFlight);

  assert.equal(
    controls(card).filter((item) => item.disabled).length,
    0,
    "a command in flight must not disable any control",
  );

  const [heater] = classed(card, "tile--pending");
  assert.ok(heater, "the control being awaited is marked pending");
  assert.equal(heater.attributes["aria-pressed"], "true", "it shows what was asked for");
  assert.equal(card.attributes["aria-busy"], "true", "the card still reports activity");
}

console.log("pool card: a command in flight never takes a control away");

/* The setpoint dial reports the confirmed target, and stays operable while a
   command is in flight: a newer value replaces the one before it, it does not
   queue behind it. */
{
  const device = pool(ALL);
  const busy = context(new Set([`${device.id}|SET_TARGET_TEMPERATURE`]));
  const [dial] = classed(renderDevice(device, busy), "dial");

  assert.ok(dial, "a released setpoint gets a dial");
  assert.equal(dial.attributes.role, "slider", "assistive technology sees a slider");
  assert.equal(dial.attributes["aria-valuenow"], "36", "the dial shows the confirmed target");
  assert.equal(dial.attributes["aria-valuemin"], "20");
  assert.equal(dial.attributes["aria-valuemax"], "40");
  assert.equal(dial.attributes.tabindex, "0", "the dial is reachable by keyboard");
}

/* Offline: nothing is offered, because nothing would reach the controller. */
{
  const device = { ...pool(ALL), availability: "OFFLINE" };
  const card = renderDevice(device, ctx);
  const [dial] = classed(card, "dial");

  assert.ok(dial.className.includes("dial--disabled"), "the dial is inert while offline");
  assert.equal(
    controls(card).filter((item) => item.disabled).length,
    4,
    "no function may be offered while the controller is unreachable",
  );
  assert.equal(classed(card, "tile--pending").length, 0, "nothing is pending on an offline device");
}

console.log("pool card: the setpoint dial follows confirmed state");

/* Last filtered: reported when witnessed, never invented. */
{
  const finished = text(
    renderDevice(
      pool(ALL, { filterPump: false, filterLastStartedAt: "2026-09-01T15:20:00+00:00" }),
      ctx,
    ),
  );
  assert.ok(finished.includes("Zuletzt gefiltert"), "the last filter run is labelled");
  assert.ok(/\d{2}:\d{2}/.test(finished), "the last filter run carries a time");

  const running = text(
    renderDevice(pool(ALL, { filterLastStartedAt: "2026-09-01T15:20:00+00:00" }), ctx),
  );
  assert.ok(running.includes("Läuft gerade"), "a pump still running says so");

  const never = text(renderDevice(pool(ALL, { filterPump: false }), ctx));
  assert.ok(never.includes("Zuletzt gefiltert"), "the fact is still shown");
  assert.ok(never.includes("Noch nicht beobachtet"), "an unwitnessed start is not invented");
}

console.log("pool card: last filter run reported only when observed");

/* The outdoor thermometer is a reading, not a control. */
{
  const card = renderDevice(
    {
      id: "00000000-0000-4000-8000-000000000001",
      displayName: "Draussen",
      kind: "SENSOR",
      roomName: null,
      capabilities: ["CURRENT_TEMPERATURE"],
      availability: "ONLINE",
      isStale: false,
      stateObservedAt: "2026-09-01T17:00:00+00:00",
      constraints: {},
      state: { currentTemperatureC: 18.4 },
    },
    ctx,
  );

  assert.ok(text(card).includes("18,4"), "the outdoor reading is shown");
  assert.equal(controls(card).length, 0, "a thermometer offers nothing to press");
}

console.log("sensor card: a reading, and nothing to press");

/* Timers reach a device with nobody watching, so what the card offers and what
   it claims are both worth pinning down. */
{
  const card = renderDevice(pool(ALL), ctx);
  const shown = text(card);

  assert.equal(classed(card, "timer").length, 1, "the released functions get a timer section");
  assert.equal(classed(card, "timer__row").length, 2, "heater and pump each get one row");
  assert.ok(shown.includes("Start in"), "a delayed start can be armed");
  assert.ok(shown.includes("Laufzeit"), "a run-for can be armed");
  assert.ok(shown.includes("genau einmal"), "the card says a timer fires once");
}

/* Nothing to schedule: no section at all, rather than an empty one. */
{
  const card = renderDevice(pool(["CURRENT_TEMPERATURE", "BUBBLES"]), ctx);
  assert.equal(classed(card, "timer").length, 0, "an unreleased function offers no timer");
}

/* An armed timer replaces its picker and can be called off. */
{
  const device = pool(ALL);
  const dueAt = new Date(Date.now() + 2 * 3600 * 1000).toISOString();
  const armed = context(
    new Set(),
    [{ id: "s1", action: "SET_HEATER", kind: "RUN_FOR", desired: false, dueAt, status: "ARMED" }],
  );
  const card = renderDevice(device, armed);
  const shown = text(card);

  assert.equal(classed(card, "timer__row--armed").length, 1, "the armed function shows its timer");
  assert.ok(shown.includes("stoppt in"), "a run-for counts down to the stop");
  assert.ok(shown.includes("Abbrechen"), "an armed timer can always be called off");
}

/* A timer whose moment has passed says so rather than showing a negative time. */
{
  const dueAt = new Date(Date.now() - 60_000).toISOString();
  const overdue = context(
    new Set(),
    [{ id: "s1", action: "SET_HEATER", kind: "DELAYED_START", desired: true, dueAt, status: "ARMED" }],
  );
  const shown = text(renderDevice(pool(ALL), overdue));

  assert.ok(!shown.includes("-"), "no negative countdown may be shown");
  assert.ok(shown.includes("jeden Moment"), "a due timer says it is about to run");
}

console.log("pool card: timers offered only for released functions, and never silent");

/* Every kind of device must render, released or not.

   This exists because rebuilding the pool card around its dial quietly deleted
   two shared helpers that lights and speakers still called. Nothing failed:
   there were no lights in the household yet. A syntax check does not catch a
   function that is merely gone, so every kind is rendered here instead. */
{
  const KINDS = [
    ["POOL", ["CURRENT_TEMPERATURE", "TARGET_TEMPERATURE", "HEATING", "FILTER", "BUBBLES"]],
    ["LIGHT", ["POWER", "BRIGHTNESS"]],
    ["SWITCH", ["POWER"]],
    ["MEDIA_PLAYER", ["MEDIA_PLAYBACK", "VOLUME"]],
    ["LOCK", ["LOCK"]],
    ["THERMOSTAT", ["CURRENT_TEMPERATURE", "TARGET_TEMPERATURE"]],
    ["SENSOR", ["CURRENT_TEMPERATURE"]],
    ["WASHING_MACHINE", ["PROGRAM_STATUS"]],
    ["DISHWASHER", ["PROGRAM_STATUS"]],
  ];

  const everything = {
    power: true,
    brightness: 60,
    currentTemperatureC: 21.5,
    targetTemperatureC: 22,
    heater: true,
    filterPump: true,
    bubbles: false,
    controlPanelLock: false,
    lockState: "LOCKED",
    volume: 35,
    playback: "PLAYING",
    program: "RUNNING",
    programName: "Eco",
    remainingSeconds: 3600,
  };

  for (const [kind, capabilities] of KINDS) {
    for (const released of [capabilities, []]) {
      const device = {
        id: `00000000-0000-4000-8000-00000000000${KINDS.length}`,
        displayName: "Something",
        kind,
        roomName: "Somewhere",
        capabilities: released,
        availability: "ONLINE",
        isStale: false,
        stateObservedAt: "2026-09-01T17:00:00+00:00",
        constraints: {
          targetTemperatureMinC: 5,
          targetTemperatureMaxC: 40,
          targetTemperatureStepC: 0.5,
        },
        state: everything,
      };

      const label = `${kind} with ${released.length ? "controls" : "nothing released"}`;
      let card;
      assert.doesNotThrow(() => {
        card = renderDevice(device, ctx);
      }, `${label} must render`);
      assert.ok(text(card).includes("Something"), `${label} must name the device`);
    }
  }
}

console.log("every device kind renders, with and without released controls");

/* An unreleased device still reports what the gateway knows about it. */
{
  const light = {
    id: "00000000-0000-4000-8000-00000000000a",
    displayName: "Ceiling Light",
    kind: "LIGHT",
    roomName: "Living Room",
    capabilities: [],
    availability: "ONLINE",
    isStale: false,
    stateObservedAt: "2026-09-01T17:00:00+00:00",
    constraints: {},
    state: { power: true, brightness: 60 },
  };
  const shown = text(renderDevice(light, ctx));

  assert.ok(shown.includes("An"), "an unreleased light still says whether it is on");
  assert.ok(shown.includes("60 %"), "and how bright it is");
  assert.ok(shown.includes("Nur Anzeige"), "and why there is nothing to press");
}

console.log("an unreleased light shows its state rather than an empty card");
