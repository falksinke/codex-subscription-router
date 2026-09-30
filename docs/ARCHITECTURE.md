# Architecture

The independently built desktop uses bundle identifier `app.cdxmux.multi`; its
Computer Use helper uses `com.cdxmux.sky.CUAService`. Neither identifier is used
by the official ChatGPT installation. These identifiers and the `.codex-mux`
state directory remain stable across the product rename to preserve connected
accounts and sticky thread ownership. Privacy-grant continuity additionally
requires the same Apple signing identity and designated requirement. The app
requires a genuine Apple certificate to preserve its native library validation.

Codex Subscription Router leaves the copied `CodexCLI.app` executable in place.
At Electron's normal bundled local desktop connection, it substitutes a small
Go multiplexer at `Contents/Resources/codex-mux`. The temporary startup-policy
connection uses the official CLI directly. `CODEX_MUX_REAL_CODEX` identifies
the official executable for account children. Configured CLI overrides bypass
the multiplexer, including overrides that point to the bundled executable.

## Request routing

The desktop app opens one JSON-RPC app-server connection to the multiplexer.
The multiplexer starts one real app-server child for every enabled account,
each with its own `CODEX_HOME` and `CODEX_SQLITE_HOME`.

New threads are assigned using a quota-urgency score: weekly percentage
remaining divided by the hours until that account resets. Banked usage resets
add a capped bonus, while short-window usage, existing pinned-thread count, and
stable account order break close results. Reset-credit metadata is fetched in
parallel, cached for five minutes, and treated as neutral when unavailable.
Once a thread ID is known, `state.json` persists its owner. Requests, responses,
approvals, and notifications are rewritten only as needed to preserve one
coherent desktop session.

If the owner is depleted, the multiplexer resumes the rollout on an account
with capacity and updates ownership. Threads do not migrate for ordinary load
balancing.

## Account isolation

The Primary account uses `~/.codex`. Added accounts use
`~/.codex-mux/accounts/<id>/codex-home`. Managed configuration is copied from
the Primary account, excluding credential-store settings and project trust.
Each isolated account forces file-backed CLI and MCP OAuth credentials.

## Desktop integration

The patcher extracts `app.asar`, verifies exact upstream anchors, inserts the
account UI, disables self-update, and repacks the archive with an updated
integrity hash. The app receives a separate Chromium profile and URL scheme.

The modified Computer Use service, Node runtime, callers, protected Electron
companions, libaperitif and Codex Framework use one selected Apple identity.
Companions and libaperitif are signed before their containing framework, and
the framework before the outer app. Their existing runtime policy is preserved;
unrelated native code retains its original signature.
The helper uses a separate bundle identity and socket, with its own macOS
privacy grants and without the official app-group container.

## Plugin behavior

Plugin definitions and managed MCP configuration are shared. The Plugins page
adds an account selector and marks Apps, MCP status, and MCP OAuth requests with
the selected account ID. The multiplexer removes that private routing marker
before forwarding the strict RPC request to the chosen child.

## Control API

The renderer calls a narrow preload bridge. Electron main verifies the sender
is the app's `app://-` main frame and proxies only approved operations to HTTP
over `~/.codex-mux/control.sock`. The state directory is owner-only and the
socket is mode `0600`; there is no production TCP listener. Electron main reads
the random 256-bit token from a private file and attaches it to socket requests.
The token never enters the renderer or its event URLs.

The service exposes account metadata, aggregated usage and profile data, thread
ownership, login/logout actions, and an authenticated SSE event stream; it never
returns OAuth tokens. Main and preload fan events out to renderer subscribers
and close streams on navigation, unsubscribe, or window destruction.
