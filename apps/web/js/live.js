/* Live state stream.

   A browser cannot set an Authorization header on a WebSocket handshake and a
   credential must never sit in a URL, so the socket is opened with a single-use
   ticket presented through Sec-WebSocket-Protocol (ADR 0011).

   The gateway drops events for a connection that falls behind and says so with
   a ResyncRequired frame. That is handled here by refetching the snapshot: a
   client must never quietly diverge from real device state. */

const SUBPROTOCOL = "homeflow.v1";
const INITIAL_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 30000;

export class LiveConnection {
  #api;
  #handlers;
  #socket = null;
  #backoff = INITIAL_BACKOFF_MS;
  #retryTimer = null;
  #stopped = true;

  /**
   * @param {import("./api.js").Api} api
   * @param {{onState: Function, onResync: Function, onStatus: Function,
   *          onSchedule: Function}} handlers
   */
  constructor(api, handlers) {
    this.#api = api;
    this.#handlers = handlers;
  }

  start() {
    this.#stopped = false;
    this.#connect();
  }

  stop() {
    this.#stopped = true;
    this.#clearRetry();
    if (this.#socket) {
      this.#socket.onclose = null;
      this.#socket.close();
      this.#socket = null;
    }
    this.#handlers.onStatus("offline");
  }

  async #connect() {
    if (this.#stopped) return;
    this.#handlers.onStatus("connecting");

    let ticket;
    try {
      ({ ticket } = await this.#api.websocketTicket());
    } catch {
      this.#scheduleRetry();
      return;
    }
    if (this.#stopped) return;

    const url = new URL("/v1/ws", window.location.href);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";

    let socket;
    try {
      socket = new WebSocket(url, [SUBPROTOCOL, `homeflow.ticket.${ticket}`]);
    } catch {
      this.#scheduleRetry();
      return;
    }
    this.#socket = socket;

    socket.onopen = () => {
      this.#backoff = INITIAL_BACKOFF_MS;
      this.#handlers.onStatus("live");
      // The socket only carries updates; the authoritative snapshot is REST.
      this.#handlers.onResync();
    };

    socket.onmessage = (event) => {
      let frame;
      try {
        frame = JSON.parse(event.data);
      } catch {
        return;
      }
      this.#dispatch(frame);
    };

    socket.onclose = () => {
      this.#socket = null;
      this.#scheduleRetry();
    };

    socket.onerror = () => {
      // onclose always follows; retry scheduling lives there.
    };
  }

  #dispatch(frame) {
    switch (frame.type) {
      case "Hello":
        return;
      case "Ping":
        this.#send({ type: "Pong" });
        return;
      case "ResyncRequired":
        this.#handlers.onResync();
        return;
      case "ScheduleArmed":
      case "ScheduleSettled":
        // A timer can be armed or fire from another phone, or fire on its own.
        this.#handlers.onSchedule?.(frame);
        if (frame.device) this.#handlers.onState(frame.device);
        return;
      default:
        if (frame.device) this.#handlers.onState(frame.device);
    }
  }

  #send(message) {
    if (this.#socket && this.#socket.readyState === WebSocket.OPEN) {
      this.#socket.send(JSON.stringify(message));
    }
  }

  #scheduleRetry() {
    if (this.#stopped || this.#retryTimer !== null) return;
    this.#handlers.onStatus("offline");
    const jitter = Math.random() * 400;
    const delay = Math.min(this.#backoff, MAX_BACKOFF_MS) + jitter;
    this.#retryTimer = window.setTimeout(() => {
      this.#retryTimer = null;
      this.#backoff = Math.min(this.#backoff * 2, MAX_BACKOFF_MS);
      this.#connect();
    }, delay);
  }

  #clearRetry() {
    if (this.#retryTimer !== null) {
      window.clearTimeout(this.#retryTimer);
      this.#retryTimer = null;
    }
  }

  /** Reconnect immediately, for example when the app returns to the foreground. */
  wake() {
    if (this.#stopped) return;
    if (this.#socket && this.#socket.readyState === WebSocket.OPEN) {
      this.#handlers.onResync();
      return;
    }
    this.#clearRetry();
    this.#backoff = INITIAL_BACKOFF_MS;
    this.#connect();
  }
}
