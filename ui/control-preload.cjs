"use strict";

// This closure is appended directly to the upstream sandboxed preload bundle.
// Keep it self-contained and limited to Electron's context bridge.
(() => {
  const { contextBridge, ipcRenderer } = require("electron");
  const REQUEST_CHANNEL = "codex-mux:control:request:v1";
  const SUBSCRIBE_CHANNEL = "codex-mux:control:subscribe:v1";
  const UNSUBSCRIBE_CHANNEL = "codex-mux:control:unsubscribe:v1";
  const EVENT_CHANNEL = "codex-mux:control:event:v1";
  const subscribers = new Set();

  ipcRenderer.on(EVENT_CHANNEL, (_event, payload) => {
    for (const subscriber of [...subscribers]) {
      try {
        subscriber(payload);
      } catch {
        // An isolated renderer callback cannot disrupt other subscribers.
      }
    }
  });

  async function request(path, options = {}) {
    if (typeof path !== "string" || options == null || typeof options !== "object") {
      throw new Error("Control request is invalid.");
    }
    const result = await ipcRenderer.invoke(REQUEST_CHANNEL, {
      path,
      method: options.method || "GET",
      body: options.body ?? null,
    });
    if (!result || result.ok !== true) {
      const message =
        result && typeof result.error === "string"
          ? result.error.slice(0, 300)
          : "Subscription control is unavailable.";
      throw new Error(message);
    }
    return result.value;
  }

  function subscribe(callback) {
    if (typeof callback !== "function") {
      throw new TypeError("Control event callback must be a function.");
    }
    subscribers.add(callback);
    if (subscribers.size === 1) ipcRenderer.send(SUBSCRIBE_CHANNEL);
    let active = true;
    return () => {
      if (!active) return;
      active = false;
      subscribers.delete(callback);
      if (subscribers.size === 0) ipcRenderer.send(UNSUBSCRIBE_CHANNEL);
    };
  }

  contextBridge.exposeInMainWorld(
    "codexMuxControl",
    Object.freeze({ request, subscribe }),
  );
})();
