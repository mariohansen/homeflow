/* The circular setpoint control.

   A hot tub has one number people actually reach for, so it gets the whole
   card: the water temperature in the middle, the target as a ring you drag.

   Two rules from the wider client apply here as well. The ring shows the
   *confirmed* target, never the value a finger is currently resting on -- while
   dragging, the readout follows the finger and the ring follows behind, and
   only the value released under the finger is submitted. And the control is
   operable without a pointer: it is a slider to assistive technology, arrow
   keys step it, and the two buttons beside it do the same thing a drag does. */

import { el, svg } from "./dom.js";

const START_ANGLE = 135;
const SWEEP = 270;
const RADIUS = 78;
const CENTRE = 100;

//: Gradient definitions are addressed by id, so two dials on one page must not
//: share one. A counter is enough; nothing outside this module refers to them.
let gradientSeq = 0;

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

function polar(angleDeg) {
  const radians = (angleDeg * Math.PI) / 180;
  return [CENTRE + RADIUS * Math.cos(radians), CENTRE + RADIUS * Math.sin(radians)];
}

function arcPath(fromValue, toValue, min, max) {
  const span = max - min || 1;
  const from = START_ANGLE + ((fromValue - min) / span) * SWEEP;
  const to = START_ANGLE + ((toValue - min) / span) * SWEEP;
  const [x0, y0] = polar(from);
  const [x1, y1] = polar(to);
  const large = to - from > 180 ? 1 : 0;
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${RADIUS} ${RADIUS} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

/** Which value a point on the dial means, or null when it means nothing. */
function valueAt(x, y, { min, max, step }) {
  const angle = (Math.atan2(y - CENTRE, x - CENTRE) * 180) / Math.PI;
  const relative = (angle - START_ANGLE + 720) % 360;
  if (relative > SWEEP) {
    // In the gap at the bottom. Snap to whichever end is nearer rather than
    // jumping the setpoint across the whole range.
    return relative > SWEEP + (360 - SWEEP) / 2 ? min : max;
  }
  const raw = min + (relative / SWEEP) * (max - min);
  return clamp(Math.round(raw / step) * step, min, max);
}

/**
 * Build the dial.
 *
 * @param {object} options
 * @param {number} options.value      confirmed target
 * @param {number|null} options.reading  measured water temperature, if known
 * @param {(value: number) => void} options.onCommit  called once, on release
 * @param {() => void} [options.onDrag]  called while a value is being chosen
 */
export function temperatureDial({
  value,
  reading,
  min,
  max,
  step,
  label,
  readingLabel,
  targetLabel,
  disabled,
  format,
  onCommit,
  onDrag,
}) {
  const safe = clamp(value, min, max);
  let chosen = safe;

  // Cold at the bottom of the sweep, warm at the top: the ring says how hard
  // the tub is being asked to work before the number is read.
  const gradientId = `dial-heat-${++gradientSeq}`;
  const gradient = svg(
    "linearGradient",
    { id: gradientId, x1: "0", y1: "1", x2: "1", y2: "0" },
    [
      svg("stop", { offset: "0%", "stop-color": "#4aa8ff" }),
      svg("stop", { offset: "45%", "stop-color": "#7f9bf5" }),
      svg("stop", { offset: "100%", "stop-color": "#ff8a7a" }),
    ],
  );

  const progress = svg("path", {
    class: "dial__progress",
    d: arcPath(min, safe, min, max),
    fill: "none",
    stroke: `url(#${gradientId})`,
  });

  const [knobX, knobY] = polar(START_ANGLE + ((safe - min) / (max - min || 1)) * SWEEP);
  const knob = svg("circle", { class: "dial__knob", cx: knobX.toFixed(2), cy: knobY.toFixed(2), r: 9 });

  const known = reading !== null && reading !== undefined;
  const centre = el("div", { class: "dial__centre" }, [
    el(
      "div",
      {
        class: "dial__reading",
        // The heading is gone from the ring, so the number carries its own name.
        role: "img",
        "aria-label": `${readingLabel}: ${known ? `${format(reading)} °C` : "—"}`,
      },
      [
        el("span", { class: "dial__number", text: known ? format(reading) : "—" }),
        el("span", { class: "dial__unit", text: "°C" }),
      ],
    ),
  ]);

  const targetValue = el("span", { class: "dial__targetValue", text: `${format(safe)} °C` });
  const target = el("div", { class: "dial__target" }, [
    el("span", { class: "dial__targetLabel", text: targetLabel }),
    targetValue,
  ]);
  centre.append(target);

  function show(next) {
    chosen = next;
    targetValue.textContent = `${format(next)} °C`;
    progress.setAttribute("d", arcPath(min, next, min, max));
    const [x, y] = polar(START_ANGLE + ((next - min) / (max - min || 1)) * SWEEP);
    knob.setAttribute("cx", x.toFixed(2));
    knob.setAttribute("cy", y.toFixed(2));
    plate.setAttribute("aria-valuenow", String(next));
    plate.setAttribute("aria-valuetext", `${format(next)} °C`);
  }

  const plate = el("div", {
    class: `dial${disabled ? " dial--disabled" : ""}`,
    role: "slider",
    tabindex: disabled ? "-1" : "0",
    "aria-label": label,
    "aria-valuemin": String(min),
    "aria-valuemax": String(max),
    "aria-valuenow": String(safe),
    "aria-valuetext": `${format(safe)} °C`,
  });

  const canvas = svg(
    "svg",
    { class: "dial__canvas", viewBox: "0 0 200 200", "aria-hidden": "true" },
    [
      svg("defs", {}, [gradient]),
      svg("path", { class: "dial__track", d: arcPath(min, max, min, max), fill: "none" }),
      progress,
      knob,
    ],
  );
  plate.append(canvas, centre);

  if (!disabled) {
    let dragging = false;

    const pick = (event) => {
      const box = plate.getBoundingClientRect();
      if (!box.width) return;
      const scale = 200 / box.width;
      show(
        valueAt((event.clientX - box.left) * scale, (event.clientY - box.top) * scale, {
          min,
          max,
          step,
        }),
      );
    };

    plate.addEventListener("pointerdown", (event) => {
      dragging = true;
      plate.setPointerCapture?.(event.pointerId);
      onDrag?.();
      pick(event);
      event.preventDefault();
    });
    plate.addEventListener("pointermove", (event) => {
      if (!dragging) return;
      onDrag?.();
      pick(event);
    });
    const release = () => {
      if (!dragging) return;
      dragging = false;
      if (chosen !== safe) onCommit(chosen);
    };
    plate.addEventListener("pointerup", release);
    plate.addEventListener("pointercancel", release);

    plate.addEventListener("keydown", (event) => {
      const delta = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1 }[event.key];
      if (delta === undefined) return;
      event.preventDefault();
      onDrag?.();
      const next = clamp(chosen + delta * step, min, max);
      show(next);
      onCommit(next);
    });
  }

  function nudge(direction) {
    const next = clamp(chosen + direction * step, min, max);
    if (next === chosen) return;
    onDrag?.();
    show(next);
    onCommit(next);
  }

  const stepper = (text, direction, ariaLabel) => {
    const button = el("button", {
      class: "stepper",
      type: "button",
      text,
      "aria-label": ariaLabel,
      onclick: () => nudge(direction),
    });
    if (disabled) button.disabled = true;
    return button;
  };

  plate.append(
    el("span", { class: "dial__end dial__end--min", text: `${Math.round(min)}°` }),
    el("span", { class: "dial__end dial__end--max", text: `${Math.round(max)}°` }),
  );

  return el("div", { class: "dialWrap" }, [
    stepper("−", -1, `${label} −`),
    plate,
    stepper("+", 1, `${label} +`),
  ]);
}
