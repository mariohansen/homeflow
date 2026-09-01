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
    },
    append(child) {
      this.children.push(child);
    },
  };
}

globalThis.document = { createElement: makeNode };

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

function switches(node) {
  return flatten(node).filter((item) => item.className === "switch");
}

const { renderDevice } = await import(pathToFileURL(renderPath).href);

const ctx = {
  isBusy: () => false,
  noticeFor: () => null,
  holdRender: () => {},
  execute: () => {},
};

function pool(capabilities) {
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
    },
  };
}

/* Read-only: every value is visible, no control is offered. */
{
  const card = renderDevice(pool(["CURRENT_TEMPERATURE"]), ctx);
  const shown = text(card);

  assert.equal(switches(card).length, 0, "no control may appear for an unreleased capability");
  for (const label of ["Heizung", "Filterpumpe", "Massagedüsen", "Bedienfeldsperre"]) {
    assert.ok(shown.includes(label), `${label} must be shown even without a control`);
  }
  assert.ok(shown.includes("An"), "an active function must read as on");
  assert.ok(shown.includes("Aus"), "an inactive function must read as off");
  assert.ok(shown.includes("27"), "the water temperature must be shown");
  assert.ok(shown.includes("Nur Anzeige"), "the reason for the missing controls must be stated");
}

/* Released: the released capability becomes a control, the rest stay read-only. */
{
  const card = renderDevice(pool(["CURRENT_TEMPERATURE", "BUBBLES"]), ctx);
  const shown = text(card);

  assert.equal(switches(card).length, 1, "exactly the released capability gets a control");
  assert.ok(shown.includes("Heizung"), "unreleased values stay visible");
  assert.ok(shown.includes("Nur Anzeige"), "the note stays while anything is read-only");
}

/* Everything released: controls only, and no note. */
{
  const card = renderDevice(
    pool(["CURRENT_TEMPERATURE", "HEATING", "FILTER", "BUBBLES", "CONTROL_PANEL_LOCK"]),
    ctx,
  );

  assert.equal(switches(card).length, 4, "every released capability gets a control");
  assert.ok(!text(card).includes("Nur Anzeige"), "the note goes away when nothing is read-only");
}

console.log("pool card: read-only state visible, controls only when released");
