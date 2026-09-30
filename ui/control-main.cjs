"use strict";

// This module is imported once by Electron's main process. The renderer only
// receives the narrow bridge installed by control-preload.cjs; the socket path,
// bearer token, HTTP headers, and raw IPC channels stay in the main process.
(() => {
  const installationKey = Symbol.for("codexMux.controlMain.installed");
  if (globalThis[installationKey]) return;
  globalThis[installationKey] = true;

  const { ipcMain } = require("electron");
  const fs = require("node:fs");
  const http = require("node:http");
  const os = require("node:os");
  const path = require("node:path");

  const REQUEST_CHANNEL = "codex-mux:control:request:v1";
  const SUBSCRIBE_CHANNEL = "codex-mux:control:subscribe:v1";
  const UNSUBSCRIBE_CHANNEL = "codex-mux:control:unsubscribe:v1";
  const EVENT_CHANNEL = "codex-mux:control:event:v1";
  const MAX_REQUEST_BYTES = 64 * 1024;
  const MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
  const MAX_EVENT_BYTES = 256 * 1024;
  const ACCOUNT_ID = /^[A-Za-z0-9_-]{1,128}$/;
  const subscriptions = new Map();

  class PublicControlError extends Error {}

  function isPlainObject(value) {
    if (value == null || typeof value !== "object" || Array.isArray(value)) {
      return false;
    }
    const prototype = Object.getPrototypeOf(value);
    return prototype === Object.prototype || prototype === null;
  }

  function hasOnlyKeys(value, allowed) {
    return Object.keys(value).every((key) => allowed.includes(key));
  }

  function requireNoBody(body) {
    if (body !== undefined && body !== null) {
      throw new PublicControlError("This control request does not accept a body.");
    }
  }

  function requireOnlyQuery(url, allowed) {
    for (const key of url.searchParams.keys()) {
      if (!allowed.includes(key) || url.searchParams.getAll(key).length !== 1) {
        throw new PublicControlError("Control request query is invalid.");
      }
    }
  }

  function validateRequest(input) {
    if (!isPlainObject(input) || typeof input.path !== "string") {
      throw new PublicControlError("Control request is invalid.");
    }
    if (input.path.length === 0 || input.path.length > 2048 || !input.path.startsWith("/")) {
      throw new PublicControlError("Control request path is invalid.");
    }
    const parsed = new URL(input.path, "http://codex-mux.invalid");
    if (parsed.origin !== "http://codex-mux.invalid" || parsed.hash) {
      throw new PublicControlError("Control request path is invalid.");
    }
    const rawMethod = input.method || "GET";
    if (typeof rawMethod !== "string") {
      throw new PublicControlError("Control request method is invalid.");
    }
    const method = rawMethod.toUpperCase();
    if (!new Set(["GET", "POST", "PATCH"]).has(method)) {
      throw new PublicControlError("Control request method is invalid.");
    }
    const body = input.body;
    const pathname = parsed.pathname;

    if (pathname === "/accounts" && method === "GET") {
      requireOnlyQuery(parsed, []);
      requireNoBody(body);
    } else if (pathname === "/accounts" && method === "POST") {
      requireOnlyQuery(parsed, []);
      if (
        !isPlainObject(body) ||
        !hasOnlyKeys(body, ["label"]) ||
        typeof body.label !== "string" ||
        body.label.trim().length === 0 ||
        body.label.length > 200
      ) {
        throw new PublicControlError("Subscription label is invalid.");
      }
    } else if (pathname === "/profile/combined" && method === "GET") {
      requireOnlyQuery(parsed, ["accountId"]);
      requireNoBody(body);
      const accountId = parsed.searchParams.get("accountId");
      if (accountId != null && !ACCOUNT_ID.test(accountId)) {
        throw new PublicControlError("Subscription identifier is invalid.");
      }
    } else if (pathname === "/thread-account" && method === "GET") {
      requireOnlyQuery(parsed, ["threadId"]);
      requireNoBody(body);
      const threadId = parsed.searchParams.get("threadId");
      if (
        threadId == null ||
        threadId.length === 0 ||
        threadId.length > 512 ||
        /[\u0000-\u001f\u007f]/.test(threadId)
      ) {
        throw new PublicControlError("Thread identifier is invalid.");
      }
    } else {
      const accountMatch = pathname.match(/^\/accounts\/([A-Za-z0-9_-]{1,128})$/);
      const loginMatch = pathname.match(/^\/accounts\/([A-Za-z0-9_-]{1,128})\/login$/);
      const logoutMatch = pathname.match(/^\/accounts\/([A-Za-z0-9_-]{1,128})\/logout$/);
      const resetsMatch = pathname.match(/^\/accounts\/([A-Za-z0-9_-]{1,128})\/rate-limit-resets$/);
      const consumeMatch = pathname.match(/^\/accounts\/([A-Za-z0-9_-]{1,128})\/rate-limit-resets\/consume$/);
      requireOnlyQuery(parsed, []);

      if (accountMatch && method === "PATCH") {
        if (
          !isPlainObject(body) ||
          !hasOnlyKeys(body, ["label", "enabled"]) ||
          Object.keys(body).length === 0 ||
          (body.label !== undefined &&
            (typeof body.label !== "string" || body.label.trim().length === 0 || body.label.length > 200)) ||
          (body.enabled !== undefined && typeof body.enabled !== "boolean")
        ) {
          throw new PublicControlError("Subscription update is invalid.");
        }
      } else if (loginMatch && method === "POST") {
        if (
          !isPlainObject(body) ||
          !hasOnlyKeys(body, ["mode"]) ||
          !new Set(["chatgpt", "chatgptDeviceCode"]).has(body.mode)
        ) {
          throw new PublicControlError("Login request is invalid.");
        }
      } else if (logoutMatch && method === "POST") {
        requireNoBody(body);
      } else if (resetsMatch && method === "GET") {
        requireNoBody(body);
      } else if (consumeMatch && method === "POST") {
        if (
          !isPlainObject(body) ||
          !hasOnlyKeys(body, ["creditId", "redeemRequestId"]) ||
          (body.creditId !== null &&
            body.creditId !== undefined &&
            (typeof body.creditId !== "string" || body.creditId.length > 256)) ||
          typeof body.redeemRequestId !== "string" ||
          body.redeemRequestId.length === 0 ||
          body.redeemRequestId.length > 256
        ) {
          throw new PublicControlError("Rate-limit reset request is invalid.");
        }
      } else {
        throw new PublicControlError("Control route is not available.");
      }
    }

    let encodedBody = null;
    if (body !== undefined && body !== null) {
      encodedBody = Buffer.from(JSON.stringify(body), "utf8");
      if (encodedBody.length > MAX_REQUEST_BYTES) {
        throw new PublicControlError("Control request body is too large.");
      }
    }
    return {
      method,
      path: `/v1${parsed.pathname}${parsed.search}`,
      body: encodedBody,
    };
  }

  function stateRoot() {
    const configured = process.env.CODEX_MUX_HOME;
    return path.resolve(configured || path.join(os.homedir(), ".codex-mux"));
  }

  function currentUID() {
    if (typeof process.geteuid !== "function") {
      throw new Error("Current user identity is unavailable.");
    }
    return process.geteuid();
  }

  function readControlToken() {
    const root = stateRoot();
    const rootInfo = fs.lstatSync(root);
    if (
      rootInfo.isSymbolicLink() ||
      !rootInfo.isDirectory() ||
      rootInfo.uid !== currentUID() ||
      (rootInfo.mode & 0o077) !== 0
    ) {
      throw new Error("Control state directory is not private.");
    }
    const tokenPath = path.join(root, "control-token");
    const tokenInfo = fs.lstatSync(tokenPath);
    if (
      tokenInfo.isSymbolicLink() ||
      !tokenInfo.isFile() ||
      tokenInfo.uid !== currentUID() ||
      (tokenInfo.mode & 0o777) !== 0o600 ||
      tokenInfo.size > 128
    ) {
      throw new Error("Control token file is not private.");
    }
    const token = fs.readFileSync(tokenPath, "utf8").trim();
    if (!/^[a-f0-9]{64}$/.test(token)) {
      throw new Error("Control token is invalid.");
    }
    return token;
  }

  function socketPath() {
    return path.join(stateRoot(), "control.sock");
  }

  function redactMessage(value, token) {
    if (typeof value !== "string" || value.length === 0) {
      return "Control request failed.";
    }
    const withoutToken = value.split(token).join("[redacted]");
    return withoutToken.replace(/[\u0000-\u001f\u007f]/g, " ").slice(0, 300);
  }

  function requestJSON(route) {
    return new Promise((resolve, reject) => {
      let settled = false;
      const succeed = (value) => {
        if (settled) return;
        settled = true;
        resolve(value);
      };
      const fail = (error) => {
        if (settled) return;
        settled = true;
        reject(error);
      };
      let token;
      try {
        token = readControlToken();
      } catch {
        fail(new PublicControlError("Subscription control is unavailable."));
        return;
      }
      const headers = {
        Accept: "application/json",
        "X-Codex-Mux-Token": token,
      };
      if (route.body) {
        headers["Content-Type"] = "application/json";
        headers["Content-Length"] = String(route.body.length);
      }
      const request = http.request(
        {
          socketPath: socketPath(),
          method: route.method,
          path: route.path,
          headers,
          agent: false,
        },
        (response) => {
          if (response.statusCode >= 300 && response.statusCode < 400) {
            response.resume();
            fail(new PublicControlError("Control redirect was rejected."));
            return;
          }
          let size = 0;
          const chunks = [];
          response.on("data", (chunk) => {
            size += chunk.length;
            if (size > MAX_RESPONSE_BYTES) {
              response.destroy(new Error("Control response is too large."));
              return;
            }
            chunks.push(chunk);
          });
          response.on("end", () => {
            let result = {};
            try {
              const encoded = Buffer.concat(chunks).toString("utf8");
              result = encoded === "" ? {} : JSON.parse(encoded);
            } catch {
              fail(new PublicControlError("Control response was invalid."));
              return;
            }
            if (response.statusCode < 200 || response.statusCode >= 300) {
              const message =
                result && typeof result.error === "string"
                  ? redactMessage(result.error, token)
                  : `Control request failed (${response.statusCode}).`;
              fail(new PublicControlError(message));
              return;
            }
            succeed(result);
          });
          response.on("aborted", () =>
            fail(new PublicControlError("Control response was interrupted.")),
          );
          response.on("error", () =>
            fail(new PublicControlError("Subscription control is unavailable.")),
          );
        },
      );
      request.setTimeout(35_000, () => request.destroy(new Error("Control request timed out.")));
      request.on("error", () =>
        fail(new PublicControlError("Subscription control is unavailable.")),
      );
      if (route.body) request.write(route.body);
      request.end();
    });
  }

  function validAppSender(sender) {
    if (!sender || sender.isDestroyed()) return false;
    try {
      const source = new URL(sender.mainFrame.url);
      return (
        source.protocol === "app:" &&
        source.hostname === "-" &&
        source.username === "" &&
        source.password === "" &&
        source.port === ""
      );
    } catch {
      return false;
    }
  }

  function validMainFrame(event) {
    const frame = event.senderFrame;
    return Boolean(frame && frame === event.sender.mainFrame && validAppSender(event.sender));
  }

  function publicFailure(error) {
    if (error instanceof PublicControlError) return error.message.slice(0, 300);
    return "Subscription control is unavailable.";
  }

  ipcMain.handle(REQUEST_CHANNEL, async (event, input) => {
    if (!validMainFrame(event)) {
      return { ok: false, error: "Control request was rejected." };
    }
    try {
      const route = validateRequest(input);
      const value = await requestJSON(route);
      if (!validMainFrame(event)) {
        return { ok: false, error: "Control request was rejected." };
      }
      return { ok: true, value };
    } catch (error) {
      if (!validMainFrame(event)) {
        return { ok: false, error: "Control request was rejected." };
      }
      return { ok: false, error: publicFailure(error) };
    }
  });

  function stopSubscription(senderID) {
    const subscription = subscriptions.get(senderID);
    if (!subscription) return;
    subscriptions.delete(senderID);
    subscription.stopped = true;
    if (subscription.retryTimer) clearTimeout(subscription.retryTimer);
    if (subscription.request) subscription.request.destroy();
    subscription.sender.removeListener("destroyed", subscription.onDestroyed);
    subscription.sender.removeListener("did-start-navigation", subscription.onNavigation);
  }

  function scheduleReconnect(subscription) {
    if (subscription.stopped || subscription.retryTimer) return;
    if (subscription.request) {
      subscription.request.destroy();
      subscription.request = null;
    }
    const delay = subscription.retryDelay;
    subscription.retryDelay = Math.min(subscription.retryDelay * 2, 30_000);
    subscription.retryTimer = setTimeout(() => {
      subscription.retryTimer = null;
      connectEvents(subscription);
    }, delay);
  }

  function connectEvents(subscription) {
    if (subscription.stopped || !validAppSender(subscription.sender)) {
      stopSubscription(subscription.sender.id);
      return;
    }
    let token;
    try {
      token = readControlToken();
    } catch {
      scheduleReconnect(subscription);
      return;
    }
    const request = http.request(
      {
        socketPath: socketPath(),
        method: "GET",
        path: "/v1/events",
        headers: {
          Accept: "text/event-stream",
          "X-Codex-Mux-Token": token,
        },
        agent: false,
      },
      (response) => {
        request.setTimeout(0);
        if (response.statusCode !== 200) {
          response.resume();
          scheduleReconnect(subscription);
          return;
        }
        subscription.retryDelay = 1_000;
        let buffer = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => {
          buffer += chunk;
          if (Buffer.byteLength(buffer, "utf8") > MAX_EVENT_BYTES) {
            response.destroy(new Error("Control event is too large."));
            return;
          }
          let boundary;
          while ((boundary = buffer.indexOf("\n\n")) !== -1) {
            const block = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            const data = block
              .split("\n")
              .filter((line) => line.startsWith("data:"))
              .map((line) => line.slice(5).trimStart())
              .join("\n");
            if (data === "") continue;
            try {
              const payload = JSON.parse(data);
              if (validAppSender(subscription.sender)) {
                subscription.sender.send(EVENT_CHANNEL, payload);
              } else {
                stopSubscription(subscription.sender.id);
              }
            } catch {
              response.destroy(new Error("Control event is invalid."));
              return;
            }
          }
        });
        response.on("end", () => scheduleReconnect(subscription));
        response.on("error", () => scheduleReconnect(subscription));
      },
    );
    subscription.request = request;
    request.setTimeout(10_000, () => request.destroy(new Error("Control stream timed out.")));
    request.on("error", () => scheduleReconnect(subscription));
    request.end();
  }

  ipcMain.on(SUBSCRIBE_CHANNEL, (event) => {
    if (!validMainFrame(event) || subscriptions.has(event.sender.id)) return;
    const subscription = {
      sender: event.sender,
      request: null,
      retryTimer: null,
      retryDelay: 1_000,
      stopped: false,
      onDestroyed: () => stopSubscription(event.sender.id),
      onNavigation: (_navigationEvent, _url, _inPlace, isMainFrame) => {
        if (isMainFrame) stopSubscription(event.sender.id);
      },
    };
    subscriptions.set(event.sender.id, subscription);
    event.sender.once("destroyed", subscription.onDestroyed);
    event.sender.on("did-start-navigation", subscription.onNavigation);
    connectEvents(subscription);
  });

  ipcMain.on(UNSUBSCRIBE_CHANNEL, (event) => {
    if (!validMainFrame(event)) return;
    stopSubscription(event.sender.id);
  });
})();
