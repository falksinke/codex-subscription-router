# Compatibility

The patcher supports one exact official macOS build. It checks the OpenAI
signature before copying the app, then requires the tested version, bundle
build, `app.asar` digest, renderer anchors, main-process anchors, and native
binary reference counts. A mismatch stops the patch before installation.

## Supported source

| Component | Required value |
| --- | --- |
| Official ChatGPT version | `26.928.21956` |
| Official bundle build | `12404` |
| `app.asar` SHA-256 | `3bda98f2265ad23677dfe0163d1cc7855beade6bef11d27f830f6663d7658406` |
| Bundle identifier | `com.openai.codex` |
| Apple signing team | `2DC432GLL2` |
| Architecture | Apple silicon (`arm64`) |
| Bundled Codex CLI | `0.159.2` |

There is no untested-source override. Supporting a later ChatGPT build requires
a deliberate port with new exact filenames, semantic anchors, native counts,
and an approved ASAR digest.

## Current-build integration

The app keeps the official bundled CLI at
`Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex`. The Electron
startup-policy connection uses that CLI directly. Only the normal long-lived
desktop app-server connection is redirected through `Resources/codex-mux`, and
only when the official local bundled resolver selected that exact CLI. The real
CLI path is passed to the router in `CODEX_MUX_REAL_CODEX`. A configured
`codex_cli_command` or nonblank `CODEX_CLI_PATH` bypasses this routing even when
it points to the bundled executable.

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

A valid, locally available Apple Development or Developer ID Application
identity is required. Apple Development includes certificates managed by
Xcode's free Personal Team, so a paid Apple Developer Program membership is not
inherently required. A free Personal Team certificate successfully signed,
installed, and launched the prior build `12246` through native main and renderer
startup after the per-target entitlement, hardened-runtime, Apple-chain, team,
and deep-strict checks passed. After the socket-routing correction, the app
opened its normal window and its profile menu displayed the connected Primary
subscription and **Add another subscription**. Additional-account switching
and failover remain untested. Ad-hoc signing is unsupported because it
cannot satisfy the protected Electron helpers' library validation.

After updating the framework integrity digest, the patcher signs
`libaperitif.dylib`, the seven companion helpers that enforce library
validation, the framework, and then the outer app with the same selected Apple
identity. Each target keeps its own signing identifier, hardened-runtime flag,
runtime version, and ordinary sanitized capabilities. The existing
`Codex (Service).app` signature and its upstream library-validation exception
remain unchanged. Finished modified code must match the selected Team ID and an
Apple certificate-chain anchor. The patcher does not weaken peer checks,
disable library validation, retain OpenAI team grants or APNs provisioning, or
copy OpenAI provisioning profiles.

The bounded installation check established normal startup and a connected
Primary subscription on build `12246`. It did not exercise additional-account
login, switching, failover, Computer Use permissions, or general application
behavior. Build `12404` has been ported by exact source inspection but has not
yet been patched, signed, installed, launched, or exercised at runtime. Unit and
end-to-end suites, typechecks and linters were not run.
