/* Typed-ish access to the canonical HomeFlow API.
   The client is served from the same origin as the gateway, so every request is
   same-origin and no CORS is involved (ADR 0011). */

import { t } from "./strings.js";

const CREDENTIAL_KEY = "homeflow.credential";

/** A failure the gateway described with a problem document. */
export class ApiError extends Error {
  constructor(type, detail, status, correlationId) {
    super(detail || t(`error.${type}`) || type);
    this.name = "ApiError";
    this.type = type;
    this.status = status;
    this.correlationId = correlationId;
  }

  /** Prefer our own wording over the gateway's, which is English. */
  get localizedMessage() {
    const key = `error.${this.type}`;
    const translated = t(key);
    return translated === key ? this.message : translated;
  }
}

export function loadCredential() {
  try {
    return window.localStorage.getItem(CREDENTIAL_KEY);
  } catch {
    return null;
  }
}

export function storeCredential(token) {
  try {
    window.localStorage.setItem(CREDENTIAL_KEY, token);
  } catch {
    /* Private mode: the session still works, it just will not be remembered. */
  }
}

export function clearCredential() {
  try {
    window.localStorage.removeItem(CREDENTIAL_KEY);
  } catch {
    /* nothing to do */
  }
}

export class Api {
  #token;

  constructor(token) {
    this.#token = token;
  }

  async #request(path, { method = "GET", body } = {}) {
    let response;
    try {
      response = await fetch(path, {
        method,
        headers: {
          Authorization: `Bearer ${this.#token}`,
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: "no-store",
        credentials: "omit",
        redirect: "error",
      });
    } catch {
      throw new ApiError("network", t("error.network"), 0, null);
    }

    if (response.status === 204) return null;

    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }

    if (!response.ok) {
      const problem = payload ?? {};
      throw new ApiError(
        problem.type ?? "internal_error",
        problem.detail ?? "",
        response.status,
        problem.correlationId ?? null,
      );
    }
    return payload;
  }

  me() {
    return this.#request("/v1/me");
  }

  devices() {
    return this.#request("/v1/devices");
  }

  activity(limit = 50) {
    return this.#request(`/v1/activity?limit=${encodeURIComponent(limit)}`);
  }

  submitCommand(deviceId, action, parameters = {}) {
    return this.#request(`/v1/devices/${encodeURIComponent(deviceId)}/commands`, {
      method: "POST",
      body: { action, parameters },
    });
  }

  /** Single-use, short-lived credential for the WebSocket handshake. */
  websocketTicket() {
    return this.#request("/v1/auth/ws-ticket", { method: "POST" });
  }
}
