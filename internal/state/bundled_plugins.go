package state

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

const bundledMarketplaceName = "openai-bundled"

var isolatedBundledPluginNames = []string{
	"computer-use",
	"unified-computer-use",
	"chrome",
}

type bundledPluginInventory struct {
	primaryHome        string
	marketplaceSource  string
	materializationKey []byte
	plugins            []bundledPluginSource
	fingerprint        string
}

type bundledPluginSource struct {
	name     string
	root     string
	versions []bundledVersionSource
	latest   string
}

type bundledVersionSource struct {
	name      string
	path      string
	signature string
}

func inspectBundledPlugins(primaryHome string) (bundledPluginInventory, error) {
	inventory := bundledPluginInventory{primaryHome: primaryHome}
	hash := sha256.New()
	marketplace := filepath.Join(primaryHome, ".tmp", "bundled-marketplaces", bundledMarketplaceName)
	if info, err := os.Lstat(marketplace); err == nil {
		if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return inventory, fmt.Errorf("bundled marketplace source is not a directory: %s", marketplace)
		}
		inventory.marketplaceSource = marketplace
		inventory.materializationKey, err = os.ReadFile(filepath.Join(marketplace, ".materialization-key"))
		if err != nil && !errors.Is(err, os.ErrNotExist) {
			return inventory, fmt.Errorf("read bundled marketplace key: %w", err)
		}
		hash.Write([]byte(marketplace))
		hash.Write(inventory.materializationKey)
	} else if !errors.Is(err, os.ErrNotExist) {
		return inventory, fmt.Errorf("inspect bundled marketplace: %w", err)
	}

	cacheRoot := filepath.Join(primaryHome, "plugins", "cache", bundledMarketplaceName)
	for _, name := range isolatedBundledPluginNames {
		plugin, err := inspectBundledPlugin(cacheRoot, name, inventory.materializationKey)
		if err != nil {
			return inventory, err
		}
		if len(plugin.versions) == 0 {
			continue
		}
		inventory.plugins = append(inventory.plugins, plugin)
		hash.Write([]byte(plugin.name + "\x00" + plugin.latest))
		for _, version := range plugin.versions {
			hash.Write([]byte(version.name + "\x00" + version.signature))
		}
	}
	inventory.fingerprint = hex.EncodeToString(hash.Sum(nil))
	return inventory, nil
}

func inspectBundledPlugin(cacheRoot, name string, materializationKey []byte) (bundledPluginSource, error) {
	plugin := bundledPluginSource{name: name, root: filepath.Join(cacheRoot, name)}
	entries, err := os.ReadDir(plugin.root)
	if errors.Is(err, os.ErrNotExist) {
		return plugin, nil
	}
	if err != nil {
		return plugin, fmt.Errorf("read bundled plugin %q: %w", name, err)
	}
	rootInfo, err := os.Lstat(plugin.root)
	if err != nil || !rootInfo.IsDir() || rootInfo.Mode()&os.ModeSymlink != 0 {
		return plugin, fmt.Errorf("bundled plugin source is not a directory: %s", plugin.root)
	}
	for _, entry := range entries {
		if entry.Name() == "latest" {
			latest, err := bundledLatestVersion(plugin.root)
			if err != nil {
				return plugin, err
			}
			plugin.latest = latest
			continue
		}
		if strings.HasPrefix(entry.Name(), ".") || !entry.IsDir() || entry.Type()&os.ModeSymlink != 0 {
			continue
		}
		versionPath := filepath.Join(plugin.root, entry.Name())
		signature, err := bundledVersionSignature(versionPath, materializationKey)
		if err != nil {
			return plugin, fmt.Errorf("inspect bundled plugin %q version %q: %w", name, entry.Name(), err)
		}
		plugin.versions = append(plugin.versions, bundledVersionSource{
			name: entry.Name(), path: versionPath, signature: signature,
		})
	}
	sort.Slice(plugin.versions, func(i, j int) bool { return plugin.versions[i].name < plugin.versions[j].name })
	if plugin.latest != "" {
		found := false
		for _, version := range plugin.versions {
			if version.name == plugin.latest {
				found = true
				break
			}
		}
		if !found {
			plugin.latest = ""
		}
	}
	return plugin, nil
}

func bundledLatestVersion(pluginRoot string) (string, error) {
	link := filepath.Join(pluginRoot, "latest")
	target, err := os.Readlink(link)
	if err != nil {
		return "", fmt.Errorf("read bundled plugin latest link: %w", err)
	}
	if !filepath.IsAbs(target) {
		target = filepath.Join(pluginRoot, target)
	}
	relative, err := filepath.Rel(pluginRoot, filepath.Clean(target))
	if err != nil || relative == "." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) || filepath.IsAbs(relative) || filepath.Dir(relative) != "." {
		return "", fmt.Errorf("bundled plugin latest link escapes plugin root: %s", link)
	}
	return relative, nil
}

func bundledVersionSignature(versionPath string, materializationKey []byte) (string, error) {
	hash := sha256.New()
	hash.Write(materializationKey)
	hash.Write([]byte(filepath.Base(versionPath)))
	for _, relative := range []string{".codex-plugin/plugin.json", ".mcp.json"} {
		contents, err := os.ReadFile(filepath.Join(versionPath, relative))
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return "", err
		}
		hash.Write([]byte(relative))
		hash.Write(contents)
	}
	return hex.EncodeToString(hash.Sum(nil)), nil
}

func (inventory bundledPluginInventory) syncTo(isolatedHome string) error {
	if isolatedHome == "" || samePath(inventory.primaryHome, isolatedHome) {
		return nil
	}
	if err := requireSafeDirectory(isolatedHome); err != nil {
		return err
	}
	if inventory.marketplaceSource != "" {
		if err := syncBundledMarketplaceLink(isolatedHome, inventory.marketplaceSource); err != nil {
			return err
		}
	}
	for _, plugin := range inventory.plugins {
		if err := syncBundledPlugin(isolatedHome, inventory.primaryHome, plugin); err != nil {
			return err
		}
	}
	return nil
}
