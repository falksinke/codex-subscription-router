package state

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

func requireSafeDirectory(path string) error {
	info, err := os.Lstat(path)
	if err != nil {
		return fmt.Errorf("inspect directory %s: %w", path, err)
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return fmt.Errorf("directory is unsafe: %s", path)
	}
	return nil
}

func ensureSafeSubdirectory(root, relative string) (string, error) {
	if filepath.IsAbs(relative) {
		return "", fmt.Errorf("subdirectory must be relative: %s", relative)
	}
	if err := requireSafeDirectory(root); err != nil {
		return "", err
	}
	current := root
	for _, component := range strings.Split(filepath.Clean(relative), string(filepath.Separator)) {
		if component == "" || component == "." || component == ".." {
			return "", fmt.Errorf("unsafe subdirectory component %q", component)
		}
		current = filepath.Join(current, component)
		info, err := os.Lstat(current)
		switch {
		case err == nil:
			if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
				return "", fmt.Errorf("subdirectory is unsafe: %s", current)
			}
		case errors.Is(err, os.ErrNotExist):
			if err := os.Mkdir(current, 0o700); err != nil {
				return "", fmt.Errorf("create subdirectory %s: %w", current, err)
			}
		default:
			return "", fmt.Errorf("inspect subdirectory %s: %w", current, err)
		}
	}
	return current, nil
}

func temporarySibling(destination, label string) (string, error) {
	file, err := os.CreateTemp(filepath.Dir(destination), "."+filepath.Base(destination)+"."+label+".*")
	if err != nil {
		return "", fmt.Errorf("reserve temporary sibling: %w", err)
	}
	name := file.Name()
	if err := file.Close(); err != nil {
		return "", err
	}
	if err := os.Remove(name); err != nil {
		return "", err
	}
	return name, nil
}

func copyDirectoryTree(source, destination string) error {
	if err := requireSafeDirectory(source); err != nil {
		return err
	}
	return filepath.WalkDir(source, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return fmt.Errorf("bundled plugin contains unsupported symlink: %s", path)
		}
		relative, err := filepath.Rel(source, path)
		if err != nil || filepath.IsAbs(relative) || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
			return fmt.Errorf("bundled plugin path escapes source: %s", path)
		}
		target := filepath.Join(destination, relative)
		info, err := entry.Info()
		if err != nil {
			return err
		}
		if entry.IsDir() {
			if err := os.Mkdir(target, info.Mode().Perm()); err != nil {
				return fmt.Errorf("copy bundled plugin directory: %w", err)
			}
			return nil
		}
		if !info.Mode().IsRegular() {
			return fmt.Errorf("bundled plugin contains unsupported file: %s", path)
		}
		return copyRegularFile(path, target, info.Mode().Perm())
	})
}

func copyRegularFile(source, destination string, mode os.FileMode) error {
	input, err := os.Open(source)
	if err != nil {
		return err
	}
	defer input.Close()
	output, err := os.OpenFile(destination, os.O_WRONLY|os.O_CREATE|os.O_EXCL, mode)
	if err != nil {
		return err
	}
	_, copyErr := io.Copy(output, input)
	closeErr := output.Close()
	if copyErr != nil {
		return copyErr
	}
	return closeErr
}

func rewriteBundledMCPHome(path, primaryHome, isolatedHome string) error {
	var document map[string]any
	if err := readJSONFile(path, &document); err != nil {
		return fmt.Errorf("read bundled MCP config: %w", err)
	}
	servers, ok := document["mcpServers"].(map[string]any)
	if !ok {
		return nil
	}
	for _, rawServer := range servers {
		server, ok := rawServer.(map[string]any)
		if !ok {
			continue
		}
		environment, ok := server["env"].(map[string]any)
		if !ok {
			continue
		}
		if value, ok := environment["CODEX_HOME"].(string); ok && value == primaryHome {
			environment["CODEX_HOME"] = isolatedHome
		}
		if value, ok := environment["NODE_REPL_TRUSTED_CODE_PATHS"].(string); ok {
			paths := filepath.SplitList(value)
			for index := range paths {
				if paths[index] == primaryHome {
					paths[index] = isolatedHome
				}
			}
			environment["NODE_REPL_TRUSTED_CODE_PATHS"] = filepath.JoinList(paths)
		}
	}
	return writeJSONFile(path, document)
}

func readJSONFile(path string, destination any) error {
	contents, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return json.Unmarshal(contents, destination)
}

func writeJSONFile(path string, value any) error {
	contents, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	if err := os.WriteFile(path, append(contents, '\n'), 0o600); err != nil {
		return fmt.Errorf("write JSON file %s: %w", path, err)
	}
	return nil
}
