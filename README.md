# Codex Subscription Router

![Multi-subscription account menu](screenshots/account-menu.png)

Use multiple ChatGPT subscriptions from one independent macOS desktop app.

Codex Subscription Router creates a locally patched copy of the official
ChatGPT app, balances new chats across connected subscriptions, and keeps every
thread on one subscription so follow-up turns retain conversation context and
benefit from account-level caching.

The official ChatGPT installation is used only as build input and is never
modified. This repository contains source code and build tooling—not OpenAI
binaries or a prebuilt application.

> [!WARNING]
> This is an unofficial, version-sensitive project. It is not affiliated with
> or supported by OpenAI. Review the source and ensure your use complies with
> the terms governing every connected subscription.

![Combined multi-account profile](screenshots/combined-profile-20px.png)

## Highlights

- **Quota-aware routing.** New chats favour weekly allowance that will expire
  sooner, with a bounded boost for accounts holding banked usage resets.
- **Sticky conversations.** Once a thread is assigned, every follow-up returns
  to the same subscription unless that subscription is depleted.
- **Automatic failover.** A depleted thread continues through another account
  with quota; if the whole pool is empty, the app shows one combined alert.
- **Native account management.** The existing profile menu shows pooled usage,
  profile photos, plan names, masked emails, and device-code sign-in.
- **Account-aware settings.** Profile statistics can be viewed together or per
  subscription, while the Plugins page can switch Apps and MCP connections
  between accounts.
- **Per-account resets.** The native rate-limit sheet shows and consumes resets
  for the selected subscription.
- **Separate macOS integrations.** The copied Appshots and Computer Use helper
  has its own identity. Reliable native access requires an Apple team-backed
  signing certificate; ad-hoc builds may lack these features.

## How it works

The patched desktop still opens one app-server connection. A small Go
multiplexer fans that connection out to one official Codex child per account.
Each child has an isolated Codex home, while the multiplexer records the owner
of every thread.

```text
Codex Subscription Router.app
        │
        │ one app-server connection
        ▼
    codex-mux
    ├── Primary       → ~/.codex
    ├── Subscription 2 → isolated Codex home
    └── Subscription 3 → isolated Codex home
             │
             └── thread ID → persistent account owner
```

New-thread routing compares the quota burn rate needed before each weekly reset,
then applies a capped banked-reset boost. Short-window usage, pinned-thread
count, and stable account order break close results. Existing threads do not
migrate merely for load balancing.

Read [the architecture](docs/ARCHITECTURE.md) for the request flow and
[the security model](docs/SECURITY-MODEL.md) for trust boundaries.

## Compatibility

Codex Subscription Router currently targets:

| Component | Supported value |
| --- | --- |
| Platform | macOS on Apple silicon |
| Official Codex app version | `26.928.20755` |
| Official bundle build | `12246` |
| Go | 1.26 or newer |
| Node.js | 22.12 or newer |

The patcher verifies the original OpenAI signature, bundle identity, Apple
silicon architecture, version, build, ASAR hash, renderer anchors, and native
binary constants before applying the patch. Unknown upstream builds are
rejected. See [Compatibility](docs/COMPATIBILITY.md) for the recorded hash and
the evidence available for this port.

## Requirements

- The official ChatGPT app installed at `/Applications/ChatGPT.app`
- Xcode Command Line Tools
- Go 1.26+
- Node.js 22.12+ and npm
- An Apple Development or Developer ID Application signing identity for
  reliable Appshots and Computer Use

A team-backed signing identity is required for reliable Appshots and Computer
Use permissions. An explicit ad-hoc installation can provide the subscription
router with that limitation.

## Install

Install this fork from a local checkout of a commit you have reviewed. Replace
`REVIEWED_COMMIT` below with that full commit SHA; the installer rejects a
different revision or a dirty checkout. It does not download or pull source code.

```sh
git clone https://github.com/falksinke/codex-subscription-router.git
cd codex-subscription-router
git checkout --detach REVIEWED_COMMIT
CODEX_SUBSCRIPTION_ROUTER_REVISION=REVIEWED_COMMIT bash install.sh
```

The installer downloads only the locked npm build dependency, with install
scripts disabled. It creates and launches a separately signed app. On an existing
installation it uses the same account state, creates a recoverable backup, and
requires signing-team continuity. Quit the router and its helper before updating;
the installer never terminates a running session automatically.

> [!TIP]
> Inspect [`install.sh`](install.sh) and the source before choosing a revision.
> Do not pipe a script from a mutable branch into a shell.

### Install via prompt

> Review the source of `https://github.com/falksinke/codex-subscription-router`, pin the reviewed commit locally, and install that revision on this Mac. Keep the official ChatGPT app and existing router state intact. Verify source and resulting app signatures, and report signing or macOS permission limitations.

### Install from a clone

```sh
CODEX_SUBSCRIPTION_ROUTER_REVISION=REVIEWED_COMMIT bash install.sh
```

This creates:

- `~/Applications/Codex Subscription Router.app`
- `~/Applications/Codex Subscription Router Computer Use.app`
- an independent desktop profile under
  `~/Library/Application Support/Codex Subscription Router`

The first valid Developer ID Application identity is selected, falling back to
an Apple Development identity. Select a certificate explicitly when needed:

```sh
CODEX_MUX_SIGNING_IDENTITY="Developer ID Application: Example Corp (TEAMID1234)" \
  CODEX_SUBSCRIPTION_ROUTER_REVISION=REVIEWED_COMMIT bash install.sh
```

Reuse the same Apple team for every rebuild. Changing teams changes the app's
designated requirement and can invalidate existing macOS privacy consent. The
patcher refuses an unexpected team change unless you deliberately pass
`--allow-signing-team-change`.

For installation without an Apple signing certificate:

```sh
CODEX_SUBSCRIPTION_ROUTER_REVISION=REVIEWED_COMMIT \
  CODEX_SUBSCRIPTION_ROUTER_ALLOW_ADHOC_SIGNING=1 bash install.sh
```

Appshots and Computer Use may not function with an ad-hoc signature.
Apple push notifications are unavailable in this local copy: the patcher
removes the official app's provisioning-dependent APNs entitlement when
re-signing the desktop executable.

## Grant macOS permissions

Open **System Settings → Privacy & Security** and grant:

| Permission | Application |
| --- | --- |
| Accessibility | Codex Subscription Router |
| Screen & System Audio Recording | Codex Subscription Router Computer Use |

When macOS offers **Quit & Reopen**, use it. If the app does not relaunch,
reopen Codex Subscription Router manually. If the Computer Use row does not
appear, press the plus button and choose
`~/Applications/Codex Subscription Router Computer Use.app`.

Do not select the official ChatGPT or Codex Computer Use helper for this build;
the independent app has its own identity and permission rows. macOS may also
request Automation access the first time Computer Use controls another app.

## Add subscriptions

1. Open the profile menu at the bottom of the sidebar.
2. Select **Add another subscription**.
3. Complete the displayed device-code sign-in in your browser.
4. Return to Codex Subscription Router and wait for the account row to appear.

While the code is visible, clicking away does not dismiss the menu. Clicking
the code copies it and opens the verification page.

The profile menu displays combined weekly usage followed by one row per
subscription. Email addresses remain masked until hovered. The final row always
starts another sign-in.

## Routing behavior

| Situation | Behaviour |
| --- | --- |
| New chat | Assigned by quota-at-risk, banked resets, and short-window pressure |
| Follow-up | Sent to the thread's persisted account owner |
| Owner depleted | Continued through another account with capacity |
| Every account depleted | Combined quota alert with the next known reset |
| Account disabled | Excluded from routing and pooled usable quota |

The subscription assigned to the current thread appears in its pinned summary.

## Profiles, plugins, and resets

**Profile statistics** begin in a combined view with overlapping account
photos. Select a photo to see only that subscription's identity and statistics;
select it again to return to the combined view.

**Settings → Plugins** includes a subscription picker. Plugin definitions and
managed MCP configuration are shared, while Apps, connection status, and OAuth
login are scoped to the selected subscription.

**Rate-limit resets** remain native to the app, with an account picker added to
the sheet. Selecting a subscription changes the displayed balance and ensures
the reset is consumed only for that account.

![Account-scoped plugin connections](screenshots/plugin-account-picker-secondary-final.png)

## Update or rebuild

The copied app's updater is disabled so an official update cannot overwrite the
patch. Update `/Applications/ChatGPT.app`, verify that the new build is listed
as compatible, then rebuild:

```sh
CODEX_SUBSCRIPTION_ROUTER_REVISION=REVIEWED_COMMIT bash install.sh
```

Quit Codex Subscription Router and its Computer Use helper first. Existing
destinations are moved to timestamped directories under `~/.codex-mux/backups`;
account state and credentials are stored outside the app bundle and remain
intact. Delete old backups manually after the rebuilt app passes the smoke test.

Build separately for each macOS user. Generated bundles contain user-specific
helper and socket paths and are not relocatable or intended for redistribution.

## Local data and security

| Path | Purpose |
| --- | --- |
| `~/.codex` | Primary credentials, conversations, and cache |
| `~/.codex-mux/state.json` | Account metadata and sticky thread ownership |
| `~/.codex-mux/accounts/<id>/codex-home` | Isolated secondary account data |
| `~/.codex-mux/control-token` | Private control token, read by Electron main |
| `~/.codex-mux/control.sock` | Owner-only Unix control socket |
| `~/.codex-mux/backups` | Recoverable app and helper backups |
| `~/Library/Application Support/Codex Subscription Router` | Independent desktop profile |

The control service uses an owner-only Unix socket inside the private state
directory. Electron main accesses it through a restricted IPC bridge; the
renderer receives neither the control token nor a network endpoint. The bridge
accepts only approved account operations from the app's main frame. OAuth tokens
stay inside their account's Codex home and are never returned by the control API.
Account directories are owner-only.

Plugin configuration is intentionally synchronized from the Primary account.
Inline secrets inside shared MCP configuration are therefore copied to each
isolated account home; the account homes are not separate secret boundaries.

Automatic failover can resume a conversation under another connected account.
Connect accounts whose workspace and data policies permit that behavior. The
router does not enforce separation between personal and work subscriptions.

See [SECURITY.md](SECURITY.md) before reporting a credential, signing, or local
control-service issue.

## Development and verification

```sh
npm ci --ignore-scripts
npm run check
npm run release:check
```

The Go backend and injected renderer have no runtime third-party dependencies.
`@electron/asar` is build-only. Deterministic UI preview routes are enabled only
when `CODEX_MUX_UI_TESTS=1` is present at launch and remain token-authenticated.

The signed-app test procedure is in [SMOKE-TEST.md](docs/SMOKE-TEST.md).
[E2E-REPORT-0.1.0.md](docs/E2E-REPORT-0.1.0.md) records an upstream run on
build 6396; it does not validate this fork's build 12246 port or private IPC
transport. The existing TCP-based diagnostic scripts also need adaptation to
the private socket before reuse.

## Known limitations

- Upstream ChatGPT updates can require new, reviewed patch anchors.
- The initial merged history fetch is limited to 500 threads per account.
- Combined “skills explored” totals can count the same skill once per account
  because the upstream profile response exposes counts rather than skill IDs.
- Generated app bundles are tied to one macOS user and signing team.
- Releases are source-only; patched OpenAI binaries are never distributed.

## Contributing and releases

Read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting changes. Releases use
the source-only process in [RELEASING.md](docs/RELEASING.md) and require a
completed signed-app smoke test for the exact tagged commit.

## License

Project source is available under the [MIT License](LICENSE). ChatGPT, Codex,
and the official macOS application are OpenAI products and are not covered by
this license.
