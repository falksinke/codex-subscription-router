package state

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

const isolatedCredentialConfig = `cli_auth_credentials_store = "file"
mcp_oauth_credentials_store = "file"`

// syncIsolatedConfig shares desktop-managed settings and MCP servers with an
// isolated subscription while keeping its credentials and project trust local.
func syncIsolatedConfig(primaryCodexHome, isolatedCodexHome string) error {
	if isolatedCodexHome == "" {
		return errors.New("isolated Codex home is required")
	}
	if err := os.MkdirAll(isolatedCodexHome, 0o700); err != nil {
		return fmt.Errorf("create isolated Codex home: %w", err)
	}
	if err := os.Chmod(isolatedCodexHome, 0o700); err != nil {
		return fmt.Errorf("secure isolated Codex home: %w", err)
	}

	primaryConfig, err := readConfig(filepath.Join(primaryCodexHome, "config.toml"))
	if err != nil {
		return fmt.Errorf("read primary config: %w", err)
	}
	configPath := filepath.Join(isolatedCodexHome, "config.toml")
	isolatedConfig, err := readConfig(configPath)
	if err != nil {
		return fmt.Errorf("read isolated config: %w", err)
	}

	managed := filterConfig(primaryConfig, func(section string) bool {
		return !isProjectSection(section)
	})
	managed = removeTopLevelCredentialSettings(managed)
	managed = rewriteBundledMarketplaceSource(managed, primaryCodexHome, isolatedCodexHome)
	projects := filterConfig(isolatedConfig, isProjectSection)

	parts := []string{isolatedCredentialConfig}
	if managed = strings.TrimSpace(managed); managed != "" {
		parts = append(parts, managed)
	}
	if projects = strings.TrimSpace(projects); projects != "" {
		parts = append(parts, projects)
	}
	contents := []byte(strings.Join(parts, "\n\n") + "\n")
	temporaryPath := configPath + ".tmp"
	if err := os.WriteFile(temporaryPath, contents, 0o600); err != nil {
		return fmt.Errorf("write temporary config: %w", err)
	}
	if err := os.Chmod(temporaryPath, 0o600); err != nil {
		return fmt.Errorf("secure temporary config: %w", err)
	}
	if err := os.Rename(temporaryPath, configPath); err != nil {
		return fmt.Errorf("commit config: %w", err)
	}
	return nil
}

func readConfig(path string) ([]byte, error) {
	contents, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	return contents, err
}

func filterConfig(contents []byte, keep func(section string) bool) string {
	var builder strings.Builder
	section := ""
	for _, line := range strings.Split(string(contents), "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "[") && strings.HasSuffix(trimmed, "]") {
			section = strings.TrimSpace(strings.TrimSuffix(strings.TrimPrefix(trimmed, "["), "]"))
		}
		if keep(section) {
			builder.WriteString(line)
			builder.WriteByte('\n')
		}
	}
	return builder.String()
}

func removeTopLevelCredentialSettings(contents string) string {
	var builder strings.Builder
	section := ""
	for _, line := range strings.Split(contents, "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "[") && strings.HasSuffix(trimmed, "]") {
			section = strings.TrimSpace(strings.TrimSuffix(strings.TrimPrefix(trimmed, "["), "]"))
		}
		if section == "" && (strings.HasPrefix(trimmed, "cli_auth_credentials_store =") ||
			strings.HasPrefix(trimmed, "mcp_oauth_credentials_store =")) {
			continue
		}
		builder.WriteString(line)
		builder.WriteByte('\n')
	}
	return builder.String()
}

func rewriteBundledMarketplaceSource(contents, primaryCodexHome, isolatedCodexHome string) string {
	lines := strings.Split(contents, "\n")
	sectionStart := -1
	for index := 0; index <= len(lines); index++ {
		if index < len(lines) {
			trimmed := strings.TrimSpace(lines[index])
			if !strings.HasPrefix(trimmed, "[") || !strings.HasSuffix(trimmed, "]") {
				continue
			}
			if sectionStart >= 0 {
				rewriteBundledMarketplaceSection(lines[sectionStart:index], primaryCodexHome, isolatedCodexHome)
			}
			section := strings.TrimSpace(strings.TrimSuffix(strings.TrimPrefix(trimmed, "["), "]"))
			if section == "marketplaces.openai-bundled" {
				sectionStart = index + 1
			} else {
				sectionStart = -1
			}
			continue
		}
		if sectionStart >= 0 {
			rewriteBundledMarketplaceSection(lines[sectionStart:index], primaryCodexHome, isolatedCodexHome)
		}
	}
	return strings.Join(lines, "\n")
}

func rewriteBundledMarketplaceSection(lines []string, primaryCodexHome, isolatedCodexHome string) {
	wantSource := "source = " + strconv.Quote(filepath.Join(primaryCodexHome, ".tmp", "bundled-marketplaces", bundledMarketplaceName))
	sourceIndex := -1
	local := false
	for index, line := range lines {
		switch strings.TrimSpace(line) {
		case `source_type = "local"`:
			local = true
		case wantSource:
			sourceIndex = index
		}
	}
	if !local || sourceIndex < 0 {
		return
	}
	indent := lines[sourceIndex][:len(lines[sourceIndex])-len(strings.TrimLeft(lines[sourceIndex], " \t"))]
	isolatedSource := filepath.Join(isolatedCodexHome, ".tmp", "bundled-marketplaces", bundledMarketplaceName)
	lines[sourceIndex] = indent + "source = " + strconv.Quote(isolatedSource)
}

func isProjectSection(section string) bool {
	return section == "projects" || strings.HasPrefix(section, "projects.")
}

func samePath(left, right string) bool {
	if left == "" || right == "" {
		return false
	}
	leftAbsolute, leftErr := filepath.Abs(left)
	rightAbsolute, rightErr := filepath.Abs(right)
	if leftErr != nil || rightErr != nil {
		return filepath.Clean(left) == filepath.Clean(right)
	}
	return filepath.Clean(leftAbsolute) == filepath.Clean(rightAbsolute)
}
