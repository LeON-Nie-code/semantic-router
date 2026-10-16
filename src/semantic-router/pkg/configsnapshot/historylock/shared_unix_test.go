//go:build !windows

package historylock

import (
	"os"
	"path/filepath"
	"strconv"
	"testing"
)

func TestSharedHistoryLockPreservesPrivateGroupAccess(t *testing.T) {
	if os.Getegid() == 0 {
		t.Skip("shared history deliberately rejects the root group")
	}
	t.Setenv("VLLM_SR_CONFIG_HISTORY_GID", strconv.Itoa(os.Getegid()))
	dir := filepath.Join(t.TempDir(), "history")
	if err := os.Mkdir(dir, 0o770); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(dir, os.ModeSetgid|0o770); err != nil {
		t.Fatal(err)
	}
	unlock, err := Lock(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer unlock()
	info, err := os.Stat(dir)
	if err != nil || info.Mode().Perm() != 0o770 || info.Mode()&os.ModeSetgid == 0 {
		t.Fatalf("shared directory lost its private group access: %v, %v", info, err)
	}
	info, err = os.Stat(filepath.Join(dir, ".lock"))
	if err != nil || info.Mode().Perm() != 0o660 {
		t.Fatalf("shared lock is not group writable and private to other users: %v, %v", info, err)
	}
}

func TestSharedHistoryRejectsRootGroup(t *testing.T) {
	t.Setenv("VLLM_SR_CONFIG_HISTORY_GID", "0")
	unlock, err := Lock(filepath.Join(t.TempDir(), "history"))
	if err == nil {
		unlock()
		t.Fatal("root group must not become a shared credential-history group")
	}
}

func TestHistoryLockRejectsSymlinkDirectory(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "history")
	if err := os.Symlink(root, dir); err != nil {
		t.Fatal(err)
	}
	unlock, err := Lock(dir)
	if err == nil {
		unlock()
		t.Fatal("a symlink must not redirect history permission changes")
	}
}

func TestSharedHistoryRejectsForeignGroupWithoutWideningAccess(t *testing.T) {
	t.Setenv("VLLM_SR_CONFIG_HISTORY_GID", strconv.Itoa(os.Getegid()+1))
	dir := filepath.Join(t.TempDir(), "history")
	if err := os.Mkdir(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	unlock, err := Lock(dir)
	if err == nil {
		unlock()
		t.Fatal("history must not adopt an unrelated group")
	}
	info, err := os.Stat(dir)
	if err != nil || info.Mode().Perm() != 0o700 {
		t.Fatalf("failed shared history widened directory access: %v %v", info, err)
	}
}

func TestHistoryLockRejectsHardLinkedPrivateFile(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "history")
	if err := os.Mkdir(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	private := filepath.Join(root, "management.token")
	if err := os.WriteFile(private, []byte("synthetic-private-token"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Link(private, filepath.Join(dir, ".lock")); err != nil {
		t.Fatal(err)
	}
	unlock, err := Lock(dir)
	if err == nil {
		unlock()
		t.Fatal("history must reject a lock linked to a private file")
	}
	info, err := os.Stat(private)
	if err != nil || info.Mode().Perm() != 0o600 {
		t.Fatalf("private file permissions changed: %v %v", info, err)
	}
}
