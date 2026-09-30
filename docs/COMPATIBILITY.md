# Compatibility

The patcher supports one exact official macOS build. It checks the OpenAI
signature before copying the app, then requires the tested version, bundle
build, `app.asar` digest, renderer anchors, main-process anchors, and native
binary reference counts. A mismatch stops the patch before installation.

## Supported source

| Component | Required value |
| --- | --- |
| Official ChatGPT version | `26.928.20755` |
| Official bundle build | `12246` |
| `app.asar` SHA-256 | `2301fba40bd8fa237ccdb1369363e1deefaf27953da2d767d428225d5e9eedee` |
| Bundle identifier | `com.openai.codex` |
| Apple signing team | `2DC432GLL2` |
| Architecture | Apple silicon (`arm64`) |
| Bundled Codex CLI | `0.159.0` |

There is no untested-source override. Supporting a later ChatGPT build requires
a deliberate port with new exact filenames, semantic anchors, native counts,
and an approved ASAR digest.

## Current-build integration

The app keeps the official bundled CLI at
`Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex`. The Electron
app-server launch is redirected through `Resources/codex-mux` only when the
official local bundled resolver selected that exact CLI. The real CLI path is
passed to the router in `CODEX_MUX_REAL_CODEX`; `CODEX_CLI_PATH` continues to
name the official binary.

The subscription controls use Electron IPC in the renderer and a private Unix
socket between Electron's main process and `codex-mux`. The renderer does not
receive the socket path or bearer token, and the patch does not add a TCP
control endpoint or relax the renderer's Content Security Policy.

The independent desktop signature preserves the ordinary Electron runtime,
automation, device, file, network, and personal-information entitlements. It
removes OpenAI's `com.apple.developer.aps-environment` entitlement because the
independent bundle cannot claim OpenAI's APNs provisioning. Push notifications
from the official OpenAI app are therefore outside this port's compatibility
boundary.

The current port modifies exact tested bundles for the desktop bootstrap,
main process, main-window preload, application network startup, account menu,
profile, usage modal, plugin settings, and local-thread summary. Every anchor
must occur exactly once before it is changed.

The patcher hashes the exact raw UTF-8 JSON header stored in the repacked ASAR
for `ElectronAsarIntegrity`. It also updates the enabled v1 integrity digest in
the one concrete Codex Framework version binary. That binary update is allowed
only when the framework contains one integrity sentinel with the enabled,
supported slot and its old digest matches the verified source plist. The
patcher changes only the 32-byte digest and does not disable an integrity fuse.

## Signing limits

A team-backed Apple development or distribution identity gives the copied app
and its modified Computer Use helper one consistent signing team. With the
explicit `--allow-adhoc-signing` option, the core account router can be built,
but Appshots and Computer Use may be unavailable because an ad-hoc signature
cannot satisfy the original team-based trust and privacy grants. The patcher
does not weaken those peer checks, retain the OpenAI APNs entitlement, or copy
OpenAI provisioning profiles.

After updating the framework integrity digest, the patcher restores the
framework signature before signing the outer app. It preserves the source
framework's hardened-runtime flag state and ordinary runtime capabilities,
while removing team-scoped OpenAI grants. Existing nested framework signatures
remain in place.

Runtime validation of build `12246` is separate from this compatibility claim.
The current port was produced by source inspection and exact-anchor checks; a
successful install and focused runtime exercise are still required before the
build can be described as validated.
