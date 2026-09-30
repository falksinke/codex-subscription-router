# Security model

## Trust boundaries

- The official Codex app is trusted build input only after strict verification
  of its Apple-anchored OpenAI signature, identity, team, and arm64 executable.
  It remains unchanged.
- The patcher has local filesystem and code-signing access by design.
- Each real Codex child receives its assigned account home, shared MCP/plugin
  configuration, and the inherited process environment.
- Electron main holds the private control token. The injected renderer receives
  only a restricted account-operation and event bridge.
- Other local users and remote origins are outside the control API boundary.
- Processes running as the same macOS user are not considered isolated from
  one another; they can already read that user's app data subject to macOS
  permissions.

## Credentials

OAuth material stays in `auth.json` under each account's Codex home. The
multiplexer reads an account token only to call the same authenticated ChatGPT
profile and rate-limit-reset endpoints used by the desktop experience. It does
not log or return tokens. State persisted by the mux contains account paths,
labels, enabled state, and thread ownership only.

The state root is mode `0700`; state, config, and control-token files are mode
`0600`. Existing control tokens are validated as 256-bit hexadecimal values and
their permissions are repaired on startup.

Plugin and MCP configuration is deliberately synchronized from the Primary
account so installed definitions remain consistent. Inline environment values
inside those definitions are therefore copied into every isolated account home
with mode `0600`; account isolation is not a separate secret boundary for
shared plugin configuration.

## Network

The production control server binds to an owner-only Unix socket in the private
state directory. It binds before account children start and fails closed when
the socket cannot be acquired safely. It has no TCP fallback. Private endpoints
require a token read from the owner-only file by Electron main, never embedded
in the renderer or an event-stream URL.

The preload exposes a fixed request/event bridge. Electron main verifies the
sender is the app's `app://-` main frame and permits only specified routes,
methods, queries, and body fields. Response sizes, event buffers, and JSON
request bodies are bounded; control redirects are rejected. Profile images must
use HTTPS but their hosts come from upstream profile data.

The project itself does not provide a telemetry or update endpoint. At runtime,
traffic beyond the private socket is performed by the official Codex children
or by the documented ChatGPT profile and rate-limit APIs. Installation downloads
the locked npm build dependency graph from the npm registry, with lifecycle
scripts disabled.

Automatic failover can transfer conversation history to another connected
account. Account homes do not enforce organizational or tenant data separation;
connect only accounts whose policies allow that behavior.

## Signing and native access

The source app and its pristine staged copy pass the OpenAI signature gate
before modification. The patcher accepts only the recorded version, build, and
ASAR digest. Native modules, the Computer Use helper, Node runtime, mux, and
final app are signed with the selected local identity and verified before
replacement. Certificate-backed builds use one Apple team. Official OpenAI
application-group and keychain entitlements are removed from modified callers.
The re-signed desktop executable also drops the official app's
`com.apple.developer.aps-environment` entitlement. This local identity has no
matching OpenAI push-notification provisioning profile, so retaining that
restricted capability can make macOS reject launch. Apple push notifications
are unavailable in the local copy; ordinary Electron permissions are retained.

The native helper's caller allowlist is patched to the selected team and the
independent desktop bundle ID. This is required for the helper's peer checks;
it does not bypass macOS Accessibility or Screen Recording consent.

An explicit ad-hoc build is available when no Apple certificate exists. It does
not provide a team-backed identity; Appshots and Computer Use may be unavailable.
The helper's caller and macOS consent checks remain in place.

## Diagnostics

`CODEX_MUX_UI_TESTS=1` enables deterministic preview and screenshot endpoints.
They are unavailable during a normal launch. The diagnostic UI bridge retains
its separate loopback listener; production account control still uses only the
private socket. Release workflows never set this variable. Legacy scripts that
targeted production TCP port 48123 require adaptation.

## Distribution

Releases contain source only. Publishing the patched `.app`, the official ASAR,
or any extracted OpenAI binary is outside this project's release process.
