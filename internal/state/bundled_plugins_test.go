package state

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestBundledPluginsMaterializeScopedAccountCopies(t *testing.T) {
	root := t.TempDir()
	primary := filepath.Join(root, "primary")
	isolated := filepath.Join(root, "isolated")
	approved := filepath.Join(root, "approved")
	createBundledFixture(t, primary)
	mustMkdirAll(t, filepath.Join(isolated, "plugins", "cache", "openai-curated"))
	mustWriteFile(t, filepath.Join(isolated, "auth.json"), "account-secret")
	mustWriteFile(t, filepath.Join(isolated, "plugins", "cache", "openai-curated", "keep"), "curated")

	inventory, err := inspectBundledPlugins(primary)
	if err != nil {
		t.Fatal(err)
	}
	if err := inventory.syncTo(isolated); err != nil {
		t.Fatal(err)
	}
	marketplace := filepath.Join(isolated, ".tmp", "bundled-marketplaces", bundledMarketplaceName)
	if target, err := os.Readlink(marketplace); err != nil || target != inventory.marketplaceSource {
		t.Fatalf("unexpected marketplace link target=%q err=%v", target, err)
	}
	for _, plugin := range isolatedBundledPluginNames {
		if _, err := os.Stat(filepath.Join(isolated, "plugins", "cache", bundledMarketplaceName, plugin, "1.0.0", ".codex-plugin", "plugin.json")); err != nil {
			t.Fatalf("plugin %q was not materialized: %v", plugin, err)
		}
	}
	if _, err := os.Stat(filepath.Join(isolated, "plugins", "cache", bundledMarketplaceName, "browser")); !os.IsNotExist(err) {
		t.Fatalf("out-of-scope browser plugin was copied: %v", err)
	}
	if contents, _ := os.ReadFile(filepath.Join(isolated, "auth.json")); string(contents) != "account-secret" {
		t.Fatalf("account credentials changed: %q", contents)
	}
	if contents, _ := os.ReadFile(filepath.Join(isolated, "plugins", "cache", "openai-curated", "keep")); string(contents) != "curated" {
		t.Fatalf("unrelated plugin cache changed: %q", contents)
	}

	mcpPath := filepath.Join(isolated, "plugins", "cache", bundledMarketplaceName, "unified-computer-use", "1.0.0", ".mcp.json")
	var document map[string]any
	if err := readJSONFile(mcpPath, &document); err != nil {
		t.Fatal(err)
	}
	environment := document["mcpServers"].(map[string]any)["cua_repl"].(map[string]any)["env"].(map[string]any)
	if environment["CODEX_HOME"] != isolated {
		t.Fatalf("CODEX_HOME was not isolated: %#v", environment)
	}
	wantTrusted := filepath.JoinList([]string{isolated, approved, primary + "-suffix"})
	if environment["NODE_REPL_TRUSTED_CODE_PATHS"] != wantTrusted || environment["KEEP"] != "unchanged" {
		t.Fatalf("trusted paths or unrelated MCP env changed: %#v", environment)
	}
}

func TestStoreBundledPluginSyncIsNoopUntilSourceMetadataChanges(t *testing.T) {
	root := t.TempDir()
	primary := filepath.Join(root, "primary")
	createBundledFixture(t, primary)
	store, err := Open(filepath.Join(root, "mux"), primary)
	if err != nil {
		t.Fatal(err)
	}
	account, err := store.AddAccount("Work")
	if err != nil {
		t.Fatal(err)
	}
	target := filepath.Join(account.CodexHome, "plugins", "cache", bundledMarketplaceName, "chrome", "1.0.0", ".codex-plugin", "plugin.json")
	before, err := os.Stat(target)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.SyncManagedConfig(); err != nil {
		t.Fatal(err)
	}
	after, err := os.Stat(target)
	if err != nil {
		t.Fatal(err)
	}
	if !os.SameFile(before, after) {
		t.Fatal("unchanged bundled plugin was recopied")
	}

	sourceManifest := filepath.Join(primary, "plugins", "cache", bundledMarketplaceName, "chrome", "1.0.0", ".codex-plugin", "plugin.json")
	mustWriteFile(t, sourceManifest, `{"name":"chrome","revision":2}`)
	if err := store.SyncManagedConfig(); err != nil {
		t.Fatal(err)
	}
	contents, err := os.ReadFile(target)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(contents), `"revision":2`) {
		t.Fatalf("manifest refresh did not propagate: %s", contents)
	}

	createBundledVersion(t, primary, "chrome", "2.0.0", false)
	sourceLatest := filepath.Join(primary, "plugins", "cache", bundledMarketplaceName, "chrome", "latest")
	if err := os.Remove(sourceLatest); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink("2.0.0", sourceLatest); err != nil {
		t.Fatal(err)
	}
	if err := store.SyncManagedConfig(); err != nil {
		t.Fatal(err)
	}
	isolatedLatest := filepath.Join(account.CodexHome, "plugins", "cache", bundledMarketplaceName, "chrome", "latest")
	if latest, err := os.Readlink(isolatedLatest); err != nil || latest != "2.0.0" {
		t.Fatalf("version refresh did not update latest: target=%q err=%v", latest, err)
	}
}

func TestBundledPluginSyncRejectsUnmanagedAndEscapingTargets(t *testing.T) {
	root := t.TempDir()
	primary := filepath.Join(root, "primary")
	createBundledFixture(t, primary)
	inventory, err := inspectBundledPlugins(primary)
	if err != nil {
		t.Fatal(err)
	}

	t.Run("unmanaged plugin", func(t *testing.T) {
		isolated := filepath.Join(root, "unmanaged")
		foreign := filepath.Join(isolated, "plugins", "cache", bundledMarketplaceName, "computer-use")
		mustMkdirAll(t, foreign)
		mustWriteFile(t, filepath.Join(foreign, "keep"), "foreign")
		if err := inventory.syncTo(isolated); err == nil || !strings.Contains(err.Error(), "unmanaged") {
			t.Fatalf("expected unmanaged conflict, got %v", err)
		}
		if contents, _ := os.ReadFile(filepath.Join(foreign, "keep")); string(contents) != "foreign" {
			t.Fatalf("foreign content changed: %q", contents)
		}
	})

	t.Run("escaping account symlink", func(t *testing.T) {
		isolated := filepath.Join(root, "escaping")
		outside := filepath.Join(root, "outside")
		mustMkdirAll(t, isolated)
		mustMkdirAll(t, outside)
		if err := os.Symlink(outside, filepath.Join(isolated, ".tmp")); err != nil {
			t.Fatal(err)
		}
		if err := inventory.syncTo(isolated); err == nil || !strings.Contains(err.Error(), "unsafe") {
			t.Fatalf("expected unsafe symlink error, got %v", err)
		}
		entries, err := os.ReadDir(outside)
		if err != nil || len(entries) != 0 {
			t.Fatalf("outside directory was modified: entries=%v err=%v", entries, err)
		}
	})
}

func createBundledFixture(t *testing.T, primary string) {
	t.Helper()
	marketplace := filepath.Join(primary, ".tmp", "bundled-marketplaces", bundledMarketplaceName)
	mustMkdirAll(t, filepath.Join(marketplace, ".agents", "plugins"))
	mustWriteFile(t, filepath.Join(marketplace, ".materialization-key"), `{"appVersion":"1"}`)
	mustWriteFile(t, filepath.Join(marketplace, ".agents", "plugins", "marketplace.json"), `{"name":"openai-bundled"}`)
	for _, plugin := range isolatedBundledPluginNames {
		createBundledVersion(t, primary, plugin, "1.0.0", plugin == "unified-computer-use")
	}
	chromeRoot := filepath.Join(primary, "plugins", "cache", bundledMarketplaceName, "chrome")
	if err := os.Symlink(filepath.Join(chromeRoot, "1.0.0"), filepath.Join(chromeRoot, "latest")); err != nil {
		t.Fatal(err)
	}
	createBundledVersion(t, primary, "browser", "1.0.0", false)
}

func createBundledVersion(t *testing.T, primary, plugin, version string, withMCP bool) {
	t.Helper()
	versionRoot := filepath.Join(primary, "plugins", "cache", bundledMarketplaceName, plugin, version)
	mustMkdirAll(t, filepath.Join(versionRoot, ".codex-plugin"))
	mustWriteFile(t, filepath.Join(versionRoot, ".codex-plugin", "plugin.json"), `{"name":"`+plugin+`"}`)
	if !withMCP {
		return
	}
	approved := filepath.Join(filepath.Dir(primary), "approved")
	document := map[string]any{"mcpServers": map[string]any{"cua_repl": map[string]any{"env": map[string]any{
		"CODEX_HOME":                   primary,
		"NODE_REPL_TRUSTED_CODE_PATHS": filepath.JoinList([]string{primary, approved, primary + "-suffix"}),
		"KEEP":                         "unchanged",
	}}}}
	contents, err := json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(versionRoot, ".mcp.json"), contents, 0o600); err != nil {
		t.Fatal(err)
	}
}

func mustMkdirAll(t *testing.T, path string) {
	t.Helper()
	if err := os.MkdirAll(path, 0o700); err != nil {
		t.Fatal(err)
	}
}

func mustWriteFile(t *testing.T, path, contents string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(contents), 0o600); err != nil {
		t.Fatal(err)
	}
}
