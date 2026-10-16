package historylock

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

func TestSharedHistoryAcrossDifferentUIDs(t *testing.T) {
	if os.Geteuid() != 0 {
		t.Skip("execute this test as root in the qualification container for real UID separation")
	}
	root, err := os.MkdirTemp("", "vsr-history-process-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(root) })
	if chmodErr := os.Chmod(root, 0o755); chmodErr != nil {
		t.Fatal(chmodErr)
	}
	dir := filepath.Join(root, "history")
	if mkdirErr := os.Mkdir(dir, 0o700); mkdirErr != nil {
		t.Fatal(mkdirErr)
	}
	if chownErr := os.Chown(dir, 0, 65532); chownErr != nil {
		t.Fatal(chownErr)
	}
	secret := filepath.Join(root, "management.token")
	if writeErr := os.WriteFile(secret, []byte("synthetic-private-management-token"), 0o600); writeErr != nil {
		t.Fatal(writeErr)
	}
	t.Setenv(SharedGroupEnv, "65532")
	unlock, err := Lock(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if unlock != nil {
			unlock()
		}
	}()
	testBinary, executableErr := os.Executable()
	if executableErr != nil {
		t.Fatal(executableErr)
	}
	command := exec.Command(testBinary, "-test.run=^TestSharedHistoryProcessHelper$", "-test.v")
	command.Env = append(os.Environ(), "VSR_HISTORY_PROCESS_DIR="+dir, "VSR_HISTORY_PRIVATE_TOKEN="+secret)
	command.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: 65532, Gid: 65532}}
	output, err := command.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	command.Stderr = os.Stderr
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = command.Process.Kill() })
	reader := bufio.NewScanner(output)
	for reader.Scan() && reader.Text() != "attempting shared lock" {
	}
	if reader.Err() != nil {
		t.Fatal(reader.Err())
	}
	done := make(chan error, 1)
	go func() { done <- command.Wait() }()
	select {
	case err := <-done:
		t.Fatalf("different-UID writer did not wait for the first writer: %v", err)
	case <-time.After(100 * time.Millisecond):
	}
	unlock()
	unlock = nil
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("different-UID writer did not acquire the released lock")
	}
	if data, err := os.ReadFile(filepath.Join(dir, "second-writer")); err != nil || string(data) != "shared history only" {
		t.Fatalf("different-UID writer failed: %q, %v", data, err)
	}
}

func TestSharedHistoryProcessHelper(t *testing.T) {
	dir := os.Getenv("VSR_HISTORY_PROCESS_DIR")
	if dir == "" {
		return
	}
	fmt.Println("attempting shared lock")
	unlock, err := Lock(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer unlock()
	if err := os.WriteFile(filepath.Join(dir, "second-writer"), []byte("shared history only"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := os.ReadFile(os.Getenv("VSR_HISTORY_PRIVATE_TOKEN")); !os.IsPermission(err) {
		t.Fatalf("non-root history writer must not read the owner's private management credential: %v", err)
	}
}
