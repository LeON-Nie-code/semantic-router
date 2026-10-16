package handlers

import (
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"testing"
)

func TestSharedConfigBackupKeepsPrivateGroupSnapshotModes(t *testing.T) {
	if runtime.GOOS == "windows" || os.Getegid() == 0 {
		t.Skip("root group is never a shared history group")
	}
	backups := filepath.Join(t.TempDir(), "history")
	t.Setenv("VLLM_SR_CONFIG_HISTORY_GID", strconv.Itoa(os.Getegid()))
	t.Setenv("VLLM_SR_CONFIG_BACKUP_DIR", backups)
	if err := ensureConfigSnapshotDir(backups); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(backups, "config.20261006-123000.yaml")
	if err := writeConfigSnapshotExclusive(path, []byte("provider: synthetic-secret\n")); err != nil {
		t.Fatal(err)
	}
	assertSnapshotMode(t, backups, 0o770)
	assertSnapshotMode(t, path, 0o660)
}

func TestReadConfigBackupRejectsLinkedPrivateFile(t *testing.T) {
	for _, link := range []string{"symlink", "hardlink"} {
		t.Run(link, func(t *testing.T) {
			root := t.TempDir()
			backupDir := configBackupDir(root)
			if err := os.MkdirAll(backupDir, 0o700); err != nil {
				t.Fatal(err)
			}
			private := filepath.Join(root, "management.token")
			if err := os.WriteFile(private, []byte("synthetic-private-token"), 0o600); err != nil {
				t.Fatal(err)
			}
			target := filepath.Join(backupDir, "config.20261006-123000.yaml")
			makeLink := os.Symlink
			if link == "hardlink" {
				makeLink = os.Link
			}
			if err := makeLink(private, target); err != nil {
				t.Skipf("filesystem cannot create fixture: %v", err)
			}
			data, err := readConfigBackup(root, "20261006-123000")
			if err == nil || len(data) != 0 {
				t.Fatalf("linked private file accepted as backup: bytes=%d err=%v", len(data), err)
			}
		})
	}
}

func TestSharedHistoryRepairDoesNotWidenExistingPrivateRecords(t *testing.T) {
	if runtime.GOOS == "windows" || os.Getegid() == 0 {
		t.Skip("POSIX non-root shared group")
	}
	root := t.TempDir()
	backups := filepath.Join(root, "history")
	t.Setenv("VLLM_SR_CONFIG_HISTORY_GID", strconv.Itoa(os.Getegid()))
	t.Setenv("VLLM_SR_CONFIG_BACKUP_DIR", backups)
	if err := ensureConfigSnapshotDir(backups); err != nil {
		t.Fatal(err)
	}
	private := filepath.Join(root, "management.token")
	if err := os.WriteFile(private, []byte("synthetic-private-token"), 0o600); err != nil {
		t.Fatal(err)
	}
	record := filepath.Join(backups, "config.20261006-123000.yaml")
	if err := os.WriteFile(record, []byte("synthetic-private-config"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Link(private, filepath.Join(backups, "config.20261006-123001.yaml")); err != nil {
		t.Fatal(err)
	}
	if err := RestrictExistingConfigSnapshots(root); err != nil {
		t.Fatal(err)
	}
	assertSnapshotMode(t, record, 0o600)
	assertSnapshotMode(t, private, 0o600)
}

func assertSnapshotPermissions(t *testing.T, configDir string) {
	t.Helper()
	if runtime.GOOS == "windows" {
		return
	}
	dir := configBackupDir(configDir)
	assertSnapshotMode(t, dir, 0o700)
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		if isConfigBackupEntry(entry) {
			assertSnapshotMode(t, filepath.Join(dir, entry.Name()), 0o600)
		}
	}
}

func assertSnapshotMode(t *testing.T, path string, expected os.FileMode) {
	t.Helper()
	if runtime.GOOS == "windows" {
		return
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != expected {
		t.Errorf("%s mode = %04o, want %04o", path, info.Mode().Perm(), expected)
	}
}

func TestConfigSnapshotUpgradePreservesSharedState(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX permission bits")
	}
	dir := t.TempDir()
	state := filepath.Join(dir, ".vllm-sr")
	backups := configBackupDir(dir)
	if err := os.MkdirAll(backups, 0o755); err != nil {
		t.Fatal(err)
	}
	old := filepath.Join(backups, "config.20260101-000000.yaml")
	dsl := archivedDSLPath(dir)
	shared := filepath.Join(state, "envoy.yaml")
	unrelated := filepath.Join(backups, "README.txt")
	for _, path := range []string{old, dsl, shared, unrelated} {
		if err := os.WriteFile(path, []byte("ordinary fixture"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	if err := RestrictExistingConfigSnapshots(dir); err != nil {
		t.Fatal(err)
	}
	assertSnapshotPermissions(t, dir)
	assertSnapshotMode(t, dsl, 0o600)
	assertSnapshotMode(t, state, 0o755)
	assertSnapshotMode(t, shared, 0o644)
	assertSnapshotMode(t, unrelated, 0o644)
	for _, path := range []string{old, dsl, shared, unrelated} {
		data, err := os.ReadFile(path)
		if err != nil || string(data) != "ordinary fixture" {
			t.Fatalf("upgrade changed contents: %s, %v", path, err)
		}
	}
}

func TestConfigSnapshotReplacementRestrictsExistingFile(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX permission bits")
	}
	dir := t.TempDir()
	path := filepath.Join(dir, "config.yaml")
	if err := os.WriteFile(path, []byte("previous"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := writeConfigSnapshot(path, []byte("replacement")); err != nil {
		t.Fatal(err)
	}
	assertSnapshotMode(t, path, 0o600)
	data, err := os.ReadFile(path)
	if err != nil || string(data) != "replacement" {
		t.Fatalf("replacement contents = %s, %v", data, err)
	}
	entries, err := os.ReadDir(dir)
	if err != nil || len(entries) != 1 {
		t.Fatalf("temporary snapshot not cleaned up: %v, %v", entries, err)
	}
}

func TestConfigSnapshotFreshDirectoryKeepsSharedParentReadable(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX permission bits")
	}
	dir := t.TempDir()
	if _, err := createConfigBackup(dir, []byte("ordinary config")); err != nil {
		t.Fatal(err)
	}
	assertSnapshotPermissions(t, dir)
	assertSnapshotMode(t, filepath.Join(dir, ".vllm-sr"), 0o755)
}
