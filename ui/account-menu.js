let codexMuxLoginActive = false;

function CodexMuxProfileMenuOpenChange(setOpen) {
  return (nextOpen) => {
    if (!nextOpen && codexMuxLoginActive) return;
    setOpen(nextOpen);
  };
}

async function codexMuxRequest(path, options = {}) {
  const bridge = globalThis.codexMuxControl;
  if (!bridge || typeof bridge.request !== "function") {
    throw new Error("Subscription control is unavailable.");
  }
  return bridge.request(path, {
    method: options.method || "GET",
    body: options.body ?? null,
  });
}

function codexMuxSubscribe(callback) {
  const bridge = globalThis.codexMuxControl;
  if (!bridge || typeof bridge.subscribe !== "function") {
    throw new Error("Subscription control is unavailable.");
  }
  return bridge.subscribe(callback);
}

const CODEX_MUX_ACCOUNT_SCOPED_PLUGIN_METHODS = new Set([
  "list-apps",
  "list-installed-apps",
  "read-apps",
  "list-mcp-server-status",
  "login-mcp-server",
]);

function codexMuxScopePluginRequest(method, params) {
  const accountId = globalThis.__codexMuxPluginAccountId;
  if (
    !accountId ||
    !CODEX_MUX_ACCOUNT_SCOPED_PLUGIN_METHODS.has(method) ||
    (params != null &&
      (typeof params !== "object" || Array.isArray(params)))
  ) {
    return params;
  }
  return { ...(params || {}), codexMuxAccountId: accountId };
}

async function codexMuxProfileData(accountId = null) {
  const query = accountId
    ? `?accountId=${encodeURIComponent(accountId)}`
    : "";
  const result = await codexMuxRequest(`/profile/combined${query}`);
  globalThis.__codexMuxCombinedProfileAccounts = result.accounts || [];
  return result.profile;
}

async function codexMuxRateLimitResets(accountId) {
  return codexMuxRequest(
    `/accounts/${encodeURIComponent(accountId)}/rate-limit-resets`,
  );
}

async function codexMuxConsumeRateLimitReset(accountId, input) {
  return codexMuxRequest(
    `/accounts/${encodeURIComponent(accountId)}/rate-limit-resets/consume`,
    {
      method: "POST",
      body: {
        creditId: input.creditId ?? null,
        redeemRequestId: input.redeemRequestId,
      },
    },
  );
}

function CodexMuxUsageModal({
  onClose,
}) {
  return (0, e7.jsx)(QLs, {
    defaultResetCreditsOpen: true,
    initialAvailableCount: 0,
    isRateLimitReached: false,
    onClose,
    onResetComplete: () => {},
  });
}

function CodexMuxUseResetAccountState() {
  const cachedAccounts = (globalThis.__codexMuxConnectedAccounts || []).filter(
    (account) => account.connected && account.enabled,
  );
  const [accounts, setAccounts] = kXc.useState(cachedAccounts);
  const [selectedId, setSelectedId] = kXc.useState("primary");
  const [resetCounts, setResetCounts] = kXc.useState({});
  const [loading, setLoading] = kXc.useState(cachedAccounts.length === 0);

  const loadAccounts = kXc.useCallback(async () => {
    const result = await codexMuxRequest("/accounts");
    const connected = (result.accounts || []).filter(
      (account) => account.connected && account.enabled,
    );
    setAccounts(connected);
    setSelectedId((current) =>
      connected.some((account) => account.id === current)
        ? current
        : connected[0]?.id || "primary",
    );
    setLoading(false);
    const entries = await Promise.all(
      connected.map(async (account) => {
        try {
          const resets = await codexMuxRateLimitResets(account.id);
          return [account.id, Math.max(0, resets.available_count || 0)];
        } catch {
          return [account.id, null];
        }
      }),
    );
    setResetCounts(Object.fromEntries(entries));
  }, []);

  kXc.useEffect(() => {
    loadAccounts().catch(() => setLoading(false));
  }, [loadAccounts]);

  kXc.useEffect(
    () => () => {
      delete window.__codexMuxResetAccountId;
      delete window.__codexMuxSelectedUsageWindows;
      delete window.__codexMuxResetAccountSelector;
    },
    [],
  );

  const selected =
    accounts.find((account) => account.id === selectedId) || accounts[0] || null;
  const activeId = selected?.id || selectedId;
  window.__codexMuxResetAccountId = activeId;
  window.__codexMuxSelectedUsageWindows = selected
    ? codexMuxUsageWindows(selected.rateLimits)
    : null;
  window.__codexMuxResetAccountSelector = (0, e7.jsx)(
    CodexMuxResetAccountSelector,
    {
      accounts,
      loading,
      resetCounts,
      selectedId: activeId,
      onSelect: setSelectedId,
    },
  );

}

function CodexMuxResetAccountSelector({
  accounts,
  loading,
  onSelect,
  resetCounts,
  selectedId,
}) {
  return (0, e7.jsxs)("div", {
    className: "pt-4",
    children: [
      (0, e7.jsx)("div", {
        className:
          "mb-2 px-1 text-xs font-medium text-token-text-secondary",
        children: "Subscription",
      }),
      (0, e7.jsx)("div", {
        className:
          "flex flex-wrap gap-2 rounded-2xl border border-token-border p-2",
        children: loading
          ? (0, e7.jsx)("div", {
              className: "px-2 py-2 text-sm text-token-text-secondary",
              children: "Loading subscriptions…",
            })
          : accounts.map((account) => {
              const selected = account.id === selectedId;
              const count = resetCounts[account.id];
              return (0, e7.jsxs)(
                "button",
                {
                  type: "button",
                  className: [
                    "flex min-w-fit items-center gap-2 rounded-xl px-3 py-2 text-left",
                    "transition-colors hover:bg-token-foreground/5",
                    selected
                      ? "bg-token-foreground/10 text-token-text-primary"
                      : "text-token-text-secondary",
                  ].join(" "),
                  "aria-pressed": selected,
                  onClick: () => onSelect(account.id),
                  children: [
                    (0, e7.jsx)(CodexMuxAccountAvatar, {
                      imageUrl: account.profileImageUrl,
                      label: account.label,
                      className: "size-7",
                    }),
                    (0, e7.jsxs)("span", {
                      className: "flex min-w-0 flex-col",
                      children: [
                        (0, e7.jsx)("span", {
                          className: "max-w-40 truncate text-sm font-medium",
                          children: account.planLabel
                            ? `${account.label} · ${account.planLabel}`
                            : account.label,
                        }),
                        (0, e7.jsx)("span", {
                          className: "text-xs text-token-text-tertiary",
                          children:
                            count == null
                              ? "Resets unavailable"
                              : count === 1
                                ? "1 reset available"
                                : `${count} resets available`,
                        }),
                      ],
                    }),
                  ],
                },
                account.id,
              );
            }),
      }),
    ],
  });
}

function CodexMuxAccountMenu() {
  const modalScope = Lo(Q);
  const [accounts, setAccounts] = kXc.useState([]);
  const [routingAccountId, setRoutingAccountId] = kXc.useState(null);
  const [routingLoaded, setRoutingLoaded] = kXc.useState(false);
  const [routingPending, setRoutingPending] = kXc.useState(false);
  const [routingPendingAccountId, setRoutingPendingAccountId] =
    kXc.useState(null);
  const routingPendingRef = kXc.useRef(false);
  const routingReadGenerationRef = kXc.useRef(0);
  const [routingError, setRoutingError] = kXc.useState("");
  const [loading, setLoading] = kXc.useState(true);
  const [busy, setBusy] = kXc.useState(false);
  const [error, setError] = kXc.useState("");
  const [login, setLogin] = kXc.useState(null);
  const [codeCopied, setCodeCopied] = kXc.useState(false);
  const loginAccountId = login?.accountId || null;

  const refresh = kXc.useCallback(async () => {
    const routingReadGeneration = ++routingReadGenerationRef.current;
    const canApplyRouting = !routingPendingRef.current;
    try {
      const result = await codexMuxRequest("/accounts");
      const nextAccounts = result.accounts || [];
      globalThis.__codexMuxConnectedAccounts = nextAccounts.filter(
        (account) => account.connected && account.enabled,
      );
      setAccounts(nextAccounts);
      if (
        canApplyRouting &&
        routingReadGeneration === routingReadGenerationRef.current
      ) {
        setRoutingAccountId(result.routing.accountId);
        setRoutingLoaded(true);
      }
      setError("");
      if (nextAccounts.some((account) => account.connected)) setLoading(false);
    } catch (requestError) {
      setError(requestError.message);
      setLoading(false);
    }
  }, []);

  kXc.useEffect(() => {
    refresh();
    let unsubscribe = () => {};
    try {
      unsubscribe = codexMuxSubscribe((payload) => {
        if (
          payload.type === "account-updated" &&
          payload.accountId === loginAccountId
        ) {
          codexMuxLoginActive = false;
          setLogin(null);
        }
        if (
          payload.type === "account-updated" ||
          payload.type === "routing-updated"
        ) {
          refresh();
        }
      });
    } catch (subscriptionError) {
      setError(subscriptionError.message);
      setLoading(false);
    }
    const warmupTimer = setTimeout(refresh, 2_000);
    const loadingDeadline = setTimeout(() => {
      refresh().finally(() => setLoading(false));
    }, 6_000);
    const timer = setInterval(refresh, 30_000);
    return () => {
      clearTimeout(warmupTimer);
      clearTimeout(loadingDeadline);
      clearInterval(timer);
      unsubscribe();
    };
  }, [refresh, loginAccountId]);

  kXc.useEffect(() => {
    if (!login) return;
    const allowEscapeDismissal = (event) => {
      if (event.key !== "Escape") return;
      codexMuxLoginActive = false;
      setLogin(null);
    };
    window.addEventListener("keydown", allowEscapeDismissal, true);
    return () => window.removeEventListener("keydown", allowEscapeDismissal, true);
  }, [login]);

  const connected = accounts.filter(
    (account) => account.connected && account.enabled,
  );
  const weightedUsage = connected.map((account) => ({
    remaining: codexMuxRemainingPercent(
      codexMuxWeeklyWindow(account.rateLimits),
    ),
    weight: codexMuxPlanWeight(account.planType),
  }));
  const hasCompleteUsage =
    connected.length > 0 &&
    weightedUsage.every(
      ({ remaining, weight }) => remaining != null && weight != null,
    );
  const totalWeight = weightedUsage.reduce(
    (total, { weight }) => total + (weight || 0),
    0,
  );
  const totalRemaining = hasCompleteUsage
    ? weightedUsage.reduce(
        (total, { remaining, weight }) => total + remaining * weight,
        0,
      ) / totalWeight
    : null;

  async function selectRoutingAccount(accountId) {
    if (
      routingPendingRef.current ||
      (routingLoaded && accountId === routingAccountId)
    ) {
      return;
    }
    routingReadGenerationRef.current += 1;
    routingPendingRef.current = true;
    setRoutingPending(true);
    setRoutingPendingAccountId(accountId);
    setRoutingError("");
    try {
      const result = await codexMuxRequest("/routing", {
        method: "PATCH",
        body: { accountId },
      });
      setRoutingAccountId(result.routing.accountId);
      setRoutingLoaded(true);
    } catch (requestError) {
      setRoutingError(requestError.message);
    } finally {
      routingPendingRef.current = false;
      setRoutingPending(false);
      setRoutingPendingAccountId(null);
    }
  }

  async function addSubscription(event) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const created = await codexMuxRequest("/accounts", {
        method: "POST",
        body: { label: `Subscription ${connected.length + 1}` },
      });
      const result = await codexMuxRequest(`/accounts/${created.account.id}/login`, {
        method: "POST",
        body: { mode: "chatgptDeviceCode" },
      });
      const pendingLogin = result.login
        ? { ...result.login, accountId: created.account.id }
        : null;
      codexMuxLoginActive = pendingLogin != null;
      setCodeCopied(false);
      setLogin(pendingLogin);
      await refresh();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  async function copyCodeAndContinue(event) {
    event.preventDefault();
    const userCode = login?.userCode || "";
    const verificationUrl = login?.verificationUrl || login?.authUrl || "";
    const copy = userCode
      ? navigator.clipboard.writeText(userCode)
      : Promise.resolve();
    if (verificationUrl) {
      try {
        const destination = new URL(verificationUrl);
        const trustedHost =
          destination.hostname === "chatgpt.com" ||
          destination.hostname === "auth.openai.com";
        if (destination.protocol !== "https:" || !trustedHost) {
          throw new Error("untrusted verification URL");
        }
        const opened = codexMuxOpenExternal({
          href: destination.href,
          initiator: "open_in_browser_bridge",
          openTarget: "external-browser",
        });
        if (opened === false) {
          throw new Error("verification page dispatch was rejected");
        }
      } catch {
        setError("The sign-in verification page could not be opened safely.");
      }
    }
    try {
      await copy;
      setCodeCopied(userCode !== "");
    } catch {
      setError("The sign-in code could not be copied.");
    }
  }

  const rows = [];
  rows.push(
    (0, e7.jsx)(
      _H,
      {
        LeftIcon: S2,
        SubText: loading
          ? "Connecting subscriptions…"
          : !hasCompleteUsage
            ? "Estimate unavailable for this mix of plans"
            : connected.length === 1
              ? "Estimated from 1 subscription by plan size"
              : `Estimated from ${connected.length} subscriptions by plan size`,
        rightIcon: (0, e7.jsx)("span", {
          className: "shrink-0 text-codex-description tabular-nums",
          children: loading
            ? "…"
            : hasCompleteUsage
              ? `${Math.round(totalRemaining)}%`
              : "–",
        }),
        onSelect: () => BW(modalScope, CodexMuxUsageModal, {}),
        children: "Estimated usage remaining",
      },
      "codex-mux-total",
    ),
  );
  rows.push(
    (0, e7.jsx)(CH.Separator, {}, "codex-mux-accounts-separator"),
  );
  const automaticSelected = routingLoaded && routingAccountId == null;
  rows.push(
    (0, e7.jsx)(
      _H,
      {
        LeftIcon: CodexMuxAutomaticIcon,
        SubText: automaticSelected
          ? "Selected for new chats"
          : "For new chats · chooses by available usage",
        rightIcon: routingPending && routingPendingAccountId == null
          ? (0, e7.jsx)("span", {
              className: "shrink-0 text-codex-description",
              children: "Saving…",
            })
          : automaticSelected
            ? (0, e7.jsx)(CodexMuxCheckIcon, {
                className: "size-4 shrink-0 text-codex-description",
              })
            : null,
        onSelect: () => selectRoutingAccount(null),
        children: "Automatic",
      },
      "codex-mux-routing-automatic",
    ),
  );

  for (const account of connected) {
    const weekly = codexMuxWeeklyWindow(account.rateLimits);
    const remaining = codexMuxRemainingPercent(weekly);
    const selected = routingLoaded && account.id === routingAccountId;
    const pending = routingPending && routingPendingAccountId === account.id;
    rows.push(
      (0, e7.jsx)(
        _H,
        {
          LeftIcon: (iconProps) =>
            (0, e7.jsx)(CodexMuxAccountAvatar, {
              ...iconProps,
              imageUrl: account.profileImageUrl,
              label: account.label,
            }),
          SubText: selected
            ? (0, e7.jsxs)(e7.Fragment, {
                children: [
                  "Selected for new chats · ",
                  account.email
                    ? (0, e7.jsx)(CodexMuxMaskedEmail, { email: account.email })
                    : account.planType || "ChatGPT subscription",
                ],
              })
            : account.email
              ? (0, e7.jsx)(CodexMuxMaskedEmail, { email: account.email })
              : account.planType || "ChatGPT subscription",
          className: "group",
          rightIcon: (0, e7.jsxs)("span", {
            className:
              "flex shrink-0 items-center gap-1.5 text-codex-description tabular-nums",
            children: [
              pending ? "Saving…" : remaining == null ? "–" : `${Math.round(remaining)}%`,
              selected
                ? (0, e7.jsx)(CodexMuxCheckIcon, { className: "size-4" })
                : null,
            ],
          }),
          onSelect: () => selectRoutingAccount(account.id),
          children: account.planLabel
            ? `${account.label} · ${account.planLabel}`
            : account.label,
        },
        `codex-mux-account-${account.id}`,
      ),
    );
  }

  if (
    routingLoaded &&
    routingAccountId != null &&
    !connected.some((account) => account.id === routingAccountId)
  ) {
    const unavailable = accounts.find(
      (account) => account.id === routingAccountId,
    );
    rows.push(
      (0, e7.jsx)(
        _H,
        {
          LeftIcon: CodexMuxAutomaticIcon,
          SubText: "Selected for new chats · Choose Automatic to reset",
          rightIcon: (0, e7.jsx)(CodexMuxCheckIcon, {
            className: "size-4 shrink-0 text-codex-description",
          }),
          children: unavailable
            ? `${unavailable.label} · Unavailable`
            : "Selected subscription unavailable",
        },
        "codex-mux-routing-unavailable",
      ),
    );
  }

  if (login) {
    rows.push(
      (0, e7.jsx)(
        _H,
        {
          LeftIcon: CodexMuxCopyIcon,
          SubText: login.userCode
            ? codeCopied
              ? `Code ${login.userCode} copied`
              : `Code ${login.userCode} · Click to copy`
            : "Finish signing in with ChatGPT",
          onSelect: copyCodeAndContinue,
          children: "Continue sign-in",
        },
        "codex-mux-login",
      ),
    );
  }

  if (error) {
    rows.push(
      (0, e7.jsx)(
        _H,
        {
          LeftIcon: S2,
          SubText: error,
          tone: "danger",
          allowWrap: true,
          subTextAllowWrap: true,
          children: "Subscription pool unavailable",
        },
        "codex-mux-error",
      ),
    );
  }

  if (routingError) {
    rows.push(
      (0, e7.jsx)(
        _H,
        {
          LeftIcon: S2,
          SubText: routingError,
          tone: "danger",
          children: "New chat subscription unchanged",
        },
        "codex-mux-routing-error",
      ),
    );
  }

  if (!loading) {
    rows.push(
      (0, e7.jsx)(
        _H,
        {
          LeftIcon: CodexMuxPlusIcon,
          onSelect: addSubscription,
          children: busy ? "Adding subscription…" : "Add another subscription",
        },
        "codex-mux-add",
      ),
    );
  }
  rows.push((0, e7.jsx)(CH.Separator, {}, "codex-mux-separator"));
  return (0, e7.jsx)(e7.Fragment, { children: rows });
}

function codexMuxWeeklyWindow(rateLimits) {
  const windows = [rateLimits?.primary, rateLimits?.secondary].filter(Boolean);
  windows.sort(
    (left, right) =>
      (left.windowDurationMins || 0) - (right.windowDurationMins || 0),
  );
  return windows.at(-1) || null;
}

function codexMuxClampPercent(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return Math.min(100, Math.max(0, value));
}

function codexMuxRemainingPercent(window) {
  const usedPercent = codexMuxClampPercent(window?.usedPercent);
  return usedPercent == null ? null : 100 - usedPercent;
}

function codexMuxPlanWeight(planType) {
  if (planType === "pro") return 20;
  if (planType === "prolite") return 5;
  return null;
}

function codexMuxUsageWindows(rateLimits) {
  return [rateLimits?.primary, rateLimits?.secondary]
    .filter(Boolean)
    .map((window) => {
      const usedPercent = codexMuxClampPercent(window.usedPercent);
      if (usedPercent == null) return null;
      return {
        usedPercent,
        remainingPercent: 100 - usedPercent,
        windowMinutes: window.windowDurationMins || 0,
        resetsAt: window.resetsAt ?? null,
      };
    })
    .filter(Boolean);
}

function CodexMuxAutomaticIcon(props) {
  return (0, e7.jsx)("svg", {
    viewBox: "0 0 20 20",
    fill: "none",
    "aria-hidden": true,
    ...props,
    children: (0, e7.jsx)("path", {
      d: "M4.25 6.5h8.5m0 0-2.5-2.5m2.5 2.5-2.5 2.5m5.5 4.5h-8.5m0 0 2.5 2.5m-2.5-2.5 2.5-2.5",
      stroke: "currentColor",
      strokeWidth: 1.5,
      strokeLinecap: "round",
      strokeLinejoin: "round",
    }),
  });
}

function CodexMuxCheckIcon(props) {
  return (0, e7.jsx)("svg", {
    viewBox: "0 0 20 20",
    fill: "none",
    "aria-hidden": true,
    ...props,
    children: (0, e7.jsx)("path", {
      d: "m4.75 10.25 3.25 3 7.25-7",
      stroke: "currentColor",
      strokeWidth: 1.75,
      strokeLinecap: "round",
      strokeLinejoin: "round",
    }),
  });
}

function CodexMuxPlusIcon(props) {
  return (0, e7.jsx)("svg", {
    viewBox: "0 0 20 20",
    fill: "none",
    "aria-hidden": true,
    ...props,
    children: (0, e7.jsx)("path", {
      d: "M10 4.25v11.5M4.25 10h11.5",
      stroke: "currentColor",
      strokeWidth: 1.5,
      strokeLinecap: "round",
    }),
  });
}

function CodexMuxCopyIcon(props) {
  return (0, e7.jsx)("svg", {
    viewBox: "0 0 20 20",
    fill: "none",
    "aria-hidden": true,
    ...props,
    children: (0, e7.jsxs)(e7.Fragment, {
      children: [
        (0, e7.jsx)("rect", {
          x: 6.25,
          y: 6.25,
          width: 9.5,
          height: 9.5,
          rx: 2,
          stroke: "currentColor",
          strokeWidth: 1.5,
        }),
        (0, e7.jsx)("path", {
          d: "M13.75 6.25V6A1.75 1.75 0 0 0 12 4.25H6A1.75 1.75 0 0 0 4.25 6v6c0 .97.78 1.75 1.75 1.75h.25",
          stroke: "currentColor",
          strokeWidth: 1.5,
          strokeLinecap: "round",
        }),
      ],
    }),
  });
}

function CodexMuxMaskedEmail({ email }) {
  return (0, e7.jsxs)(e7.Fragment, {
    children: [
      (0, e7.jsx)("span", {
        className: "group-hover:hidden",
        children: "••••••••",
      }),
      (0, e7.jsx)("span", {
        className: "hidden group-hover:inline",
        children: email,
      }),
    ],
  });
}

function CodexMuxAccountAvatar({ imageUrl, label, className }) {
  const [failed, setFailed] = kXc.useState(false);
  const resolvedImageUrl = jLa(imageUrl || null);
  if (resolvedImageUrl && !failed) {
    return (0, e7.jsx)("img", {
      src: resolvedImageUrl,
      alt: "",
      className: `${className || "icon-sm"} rounded-full object-cover`,
      referrerPolicy: "no-referrer",
      onError: () => setFailed(true),
    });
  }
  const initials = label
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
  return (0, e7.jsx)("span", {
    className: `${className || "icon-sm"} flex items-center justify-center rounded-full bg-token-charts-purple/10 text-[9px] leading-none text-token-charts-purple`,
    "aria-hidden": true,
    children: initials || "?",
  });
}

function CodexMuxOverlappingAvatars({ accounts, size = "size-20" }) {
  const overlapClass = size === "size-20" ? "-ml-10" : "-ml-2";
  return (0, e7.jsx)("div", {
    className: "flex items-center justify-center",
    children: accounts.map((account, index) =>
      (0, e7.jsx)(
        "span",
        {
          className: `${index === 0 ? "" : overlapClass} rounded-full border-4 border-token-bg-primary`,
          title: account.planLabel
            ? `${account.label} · ${account.planLabel}`
            : account.label,
          children: (0, e7.jsx)(CodexMuxAccountAvatar, {
            imageUrl: account.profileImageUrl,
            label: account.label,
            className: size,
          }),
        },
        account.id,
      ),
    ),
  });
}

function CodexMuxProfileAvatarStack({ onSelect }) {
  const [accounts, setAccounts] = kXc.useState(
    globalThis.__codexMuxCombinedProfileAccounts || [],
  );
  const [selectedId, setSelectedId] = kXc.useState(
    globalThis.__codexMuxSelectedProfileAccountId || null,
  );
  kXc.useEffect(() => {
    let live = true;
    codexMuxRequest("/accounts")
      .then((result) => {
        if (!live) return;
        const connected = (result.accounts || []).filter(
          (account) => account.connected && account.enabled,
        );
        globalThis.__codexMuxCombinedProfileAccounts = connected;
        setAccounts(connected);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);
  kXc.useEffect(() => {
    globalThis.__codexMuxSelectedProfileAccountId = null;
    setSelectedId(null);
    onSelect?.();
    return () => {
      globalThis.__codexMuxSelectedProfileAccountId = null;
    };
  }, []);
  if (accounts.length === 0) return null;
  const visibleAccounts = selectedId
    ? accounts.filter((account) => account.id === selectedId)
    : accounts;
  return (0, e7.jsx)("div", {
    className: "mb-4",
    "aria-label": selectedId
      ? "Selected subscription profile"
      : `${accounts.length} connected subscriptions`,
    children: (0, e7.jsx)("div", {
      className: "flex items-center justify-center",
      children: visibleAccounts.map((account, index) =>
        (0, e7.jsx)(
          "button",
          {
            type: "button",
            className: `${index === 0 ? "" : "-ml-5"} rounded-full border-4 border-token-bg-primary transition-transform hover:z-10 hover:scale-105 focus-visible:z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-token-focus-border`,
            style: {
              marginLeft: index === 0 ? 0 : -20,
              zIndex: index,
            },
            "aria-label": selectedId
              ? `Show combined profile stats`
              : `Show ${account.label} profile stats`,
            title: account.planLabel
              ? `${account.label} · ${account.planLabel}`
              : account.label,
            onClick: () => {
              const nextId = selectedId === account.id ? null : account.id;
              globalThis.__codexMuxSelectedProfileAccountId = nextId;
              setSelectedId(nextId);
              onSelect?.();
            },
            children: (0, e7.jsx)(CodexMuxAccountAvatar, {
              imageUrl: account.profileImageUrl,
              label: account.label,
              className: "size-20",
            }),
          },
          account.id,
        ),
      ),
    }),
  });
}

function CodexMuxPluginScope() {
  const [accounts, setAccounts] = kXc.useState([]);
  const [selectedId, setSelectedId] = kXc.useState("primary");
  const [loading, setLoading] = kXc.useState(true);
  const queryClient = lt();
  kXc.useEffect(() => {
    let live = true;
    codexMuxRequest("/accounts")
      .then((result) => {
        if (!live) return;
        setAccounts(
          (result.accounts || []).filter(
            (account) => account.connected && account.enabled,
          ),
        );
      })
      .catch(() => {})
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, []);

  kXc.useEffect(() => {
    globalThis.__codexMuxPluginAccountId = selectedId;
    return () => {
      delete globalThis.__codexMuxPluginAccountId;
    };
  }, [selectedId]);

  async function selectAccount(accountId) {
    if (accountId === selectedId) return;
    globalThis.__codexMuxPluginAccountId = accountId;
    setSelectedId(accountId);
    await queryClient.invalidateQueries({
      predicate: (query) => {
        const root = query.queryKey?.[0];
        return root === "apps" || root === "plugins" || root === "mcp";
      },
    });
  }

  const selected =
    accounts.find((account) => account.id === selectedId) || accounts[0] || null;

  return (0, e7.jsxs)("div", {
    className:
      "mb-5 rounded-2xl border border-token-border-light p-3",
    children: [
      (0, e7.jsxs)("div", {
        className: "px-1",
        children: [
          (0, e7.jsx)("div", {
            className: "text-sm font-medium text-token-text-primary",
            children: "Plugin connections",
          }),
          (0, e7.jsx)("div", {
            className: "mt-0.5 text-xs text-token-text-secondary",
            children: selected
              ? `Installs are shared. Connection access below is for ${selected.label}.`
              : "Installs are shared. Choose a subscription for connection access.",
          }),
        ],
      }),
      loading
        ? (0, e7.jsx)("div", {
            className: "mt-3 px-1 text-sm text-token-text-tertiary",
            children: "Loading subscriptions…",
          })
        : (0, e7.jsx)("div", {
            className: "mt-3 flex flex-wrap gap-2",
            children: accounts.map((account) => {
              const active = account.id === selected?.id;
              return (0, e7.jsxs)(
                "button",
                {
                  type: "button",
                  className: [
                    "flex items-center gap-2 rounded-xl px-2.5 py-2 text-sm transition-colors",
                    active
                      ? "bg-token-foreground/10 text-token-text-primary"
                      : "text-token-text-secondary hover:bg-token-foreground/5",
                  ].join(" "),
                  "aria-pressed": active,
                  onClick: () => selectAccount(account.id),
                  children: [
                    (0, e7.jsx)(CodexMuxAccountAvatar, {
                      imageUrl: account.profileImageUrl,
                      label: account.label,
                      className: "size-7",
                    }),
                    (0, e7.jsx)("span", {
                      children: account.planLabel
                        ? `${account.label} · ${account.planLabel}`
                        : account.label,
                    }),
                  ],
                },
                account.id,
              );
            }),
          }),
    ],
  });
}

// The thread summary is emitted into a separate lazy-loaded renderer chunk.
// Export the same avatar component so both surfaces share image resolution,
// error handling, and the initials fallback.
globalThis.CodexMuxAccountAvatar = CodexMuxAccountAvatar;
globalThis.codexMuxProfileData = codexMuxProfileData;
globalThis.CodexMuxProfileAvatarStack = (props) =>
  (0, e7.jsx)(CodexMuxProfileAvatarStack, props || {});
globalThis.CodexMuxPluginScope = () =>
  (0, e7.jsx)(CodexMuxPluginScope, {});
