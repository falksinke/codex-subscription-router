package state

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

const (
	bundledPluginOwnerFile  = ".codex-router-managed.json"
	bundledVersionOwnerFile = ".codex-router-source.json"
)

type bundledPluginOwner struct {
	Version int    `json:"version"`
	Plugin  string `json:"plugin"`
}

type bundledVersionOwner struct {
	Version   int    `json:"version"`
	Signature string `json:"signature"`
}

func syncBundledMarketplaceLink(isolatedHome, source string) error {
	parent, err := ensureSafeSubdirectory(isolatedHome, filepath.Join(".tmp", "bundled-marketplaces"))
	if err != nil {
		return err
	}
	destination := filepath.Join(parent, bundledMarketplaceName)
	info, err := os.Lstat(destination)
	if err == nil {
		if info.Mode()&os.ModeSymlink == 0 {
			return fmt.Errorf("bundled marketplace destination is unmanaged: %s", destination)
		}
		target, readErr := os.Readlink(destination)
		if readErr != nil {
			return fmt.Errorf("read bundled marketplace destination: %w", readErr)
		}
		if target != source {
			return fmt.Errorf("bundled marketplace destination has conflicting target: %s", destination)
		}
		return nil
	}
	if !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("inspect bundled marketplace destination: %w", err)
	}
	temporary, err := temporarySibling(destination, "marketplace")
	if err != nil {
		return err
	}
	defer os.Remove(temporary)
	if err := os.Symlink(source, temporary); err != nil {
		return fmt.Errorf("stage bundled marketplace link: %w", err)
	}
	if err := os.Rename(temporary, destination); err != nil {
		return fmt.Errorf("publish bundled marketplace link: %w", err)
	}
	return nil
}

func syncBundledPlugin(isolatedHome, primaryHome string, source bundledPluginSource) error {
	cacheRoot, err := ensureSafeSubdirectory(isolatedHome, filepath.Join("plugins", "cache", bundledMarketplaceName))
	if err != nil {
		return err
	}
	destination := filepath.Join(cacheRoot, source.name)
	if _, err := os.Lstat(destination); errors.Is(err, os.ErrNotExist) {
		return stageNewBundledPlugin(destination, primaryHome, isolatedHome, source)
	} else if err != nil {
		return fmt.Errorf("inspect bundled plugin destination: %w", err)
	}
	if err := requireManagedPlugin(destination, source.name); err != nil {
		return err
	}
	for _, version := range source.versions {
		if err := syncBundledVersion(destination, primaryHome, isolatedHome, version); err != nil {
			return fmt.Errorf("sync bundled plugin %q version %q: %w", source.name, version.name, err)
		}
	}
	return syncBundledLatest(destination, source.latest)
}

func stageNewBundledPlugin(destination, primaryHome, isolatedHome string, source bundledPluginSource) error {
	stage, err := temporarySibling(destination, "plugin")
	if err != nil {
		return err
	}
	defer os.RemoveAll(stage)
	if err := os.Mkdir(stage, 0o700); err != nil {
		return fmt.Errorf("create bundled plugin stage: %w", err)
	}
	if err := writeJSONFile(filepath.Join(stage, bundledPluginOwnerFile), bundledPluginOwner{Version: 1, Plugin: source.name}); err != nil {
		return err
	}
	for _, version := range source.versions {
		versionDestination := filepath.Join(stage, version.name)
		if err := copyBundledVersion(version.path, versionDestination, primaryHome, isolatedHome, version.signature); err != nil {
			return fmt.Errorf("stage bundled plugin %q version %q: %w", source.name, version.name, err)
		}
	}
	if source.latest != "" {
		if err := os.Symlink(source.latest, filepath.Join(stage, "latest")); err != nil {
			return fmt.Errorf("stage bundled plugin latest link: %w", err)
		}
	}
	if err := os.Rename(stage, destination); err != nil {
		return fmt.Errorf("publish bundled plugin %q: %w", source.name, err)
	}
	return nil
}

func syncBundledVersion(pluginDestination, primaryHome, isolatedHome string, source bundledVersionSource) error {
	destination := filepath.Join(pluginDestination, source.name)
	owner, err := readVersionOwner(destination)
	if errors.Is(err, os.ErrNotExist) {
		stage, stageErr := temporarySibling(destination, "version")
		if stageErr != nil {
			return stageErr
		}
		defer os.RemoveAll(stage)
		if err := copyBundledVersion(source.path, stage, primaryHome, isolatedHome, source.signature); err != nil {
			return err
		}
		return os.Rename(stage, destination)
	}
	if err != nil {
		return err
	}
	if owner.Signature == source.signature {
		return nil
	}
	stage, err := temporarySibling(destination, "version")
	if err != nil {
		return err
	}
	defer os.RemoveAll(stage)
	if err := copyBundledVersion(source.path, stage, primaryHome, isolatedHome, source.signature); err != nil {
		return err
	}
	backup, err := temporarySibling(destination, "backup")
	if err != nil {
		return err
	}
	if err := os.Rename(destination, backup); err != nil {
		return fmt.Errorf("move previous managed bundled version: %w", err)
	}
	if err := os.Rename(stage, destination); err != nil {
		_ = os.Rename(backup, destination)
		return fmt.Errorf("publish refreshed bundled version: %w", err)
	}
	if err := os.RemoveAll(backup); err != nil {
		return fmt.Errorf("remove previous managed bundled version: %w", err)
	}
	return nil
}

func syncBundledLatest(pluginDestination, latest string) error {
	if latest == "" {
		return nil
	}
	destination := filepath.Join(pluginDestination, "latest")
	if _, err := readVersionOwner(filepath.Join(pluginDestination, latest)); err != nil {
		return fmt.Errorf("latest bundled version %q is not managed: %w", latest, err)
	}
	info, err := os.Lstat(destination)
	if err == nil {
		if info.Mode()&os.ModeSymlink == 0 {
			return fmt.Errorf("bundled plugin latest destination is unmanaged: %s", destination)
		}
		current, readErr := os.Readlink(destination)
		if readErr != nil {
			return readErr
		}
		if current == latest {
			return nil
		}
		if filepath.IsAbs(current) || filepath.Dir(current) != "." || current == "." || current == ".." {
			return fmt.Errorf("bundled plugin latest destination is unsafe: %s", destination)
		}
		if _, ownerErr := readVersionOwner(filepath.Join(pluginDestination, filepath.Base(current))); ownerErr != nil {
			return fmt.Errorf("bundled plugin latest destination is unmanaged: %s", destination)
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	temporary, err := temporarySibling(destination, "latest")
	if err != nil {
		return err
	}
	defer os.Remove(temporary)
	if err := os.Symlink(latest, temporary); err != nil {
		return err
	}
	return os.Rename(temporary, destination)
}

func requireManagedPlugin(path, plugin string) error {
	info, err := os.Lstat(path)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return fmt.Errorf("bundled plugin destination is unsafe: %s", path)
	}
	var owner bundledPluginOwner
	if err := readJSONFile(filepath.Join(path, bundledPluginOwnerFile), &owner); err != nil || owner.Version != 1 || owner.Plugin != plugin {
		return fmt.Errorf("bundled plugin destination is unmanaged: %s", path)
	}
	return nil
}

func readVersionOwner(path string) (bundledVersionOwner, error) {
	var owner bundledVersionOwner
	info, err := os.Lstat(path)
	if err != nil {
		return owner, err
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return owner, fmt.Errorf("bundled version destination is unsafe: %s", path)
	}
	if err := readJSONFile(filepath.Join(path, bundledVersionOwnerFile), &owner); err != nil {
		return owner, fmt.Errorf("bundled version destination is unmanaged: %s", path)
	}
	if owner.Version != 1 || owner.Signature == "" {
		return owner, fmt.Errorf("bundled version destination has invalid ownership: %s", path)
	}
	return owner, nil
}

func copyBundledVersion(source, destination, primaryHome, isolatedHome, signature string) error {
	if err := copyDirectoryTree(source, destination); err != nil {
		return err
	}
	mcpPath := filepath.Join(destination, ".mcp.json")
	if _, err := os.Lstat(mcpPath); err == nil {
		if err := rewriteBundledMCPHome(mcpPath, primaryHome, isolatedHome); err != nil {
			return err
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return writeJSONFile(filepath.Join(destination, bundledVersionOwnerFile), bundledVersionOwner{Version: 1, Signature: signature})
}
