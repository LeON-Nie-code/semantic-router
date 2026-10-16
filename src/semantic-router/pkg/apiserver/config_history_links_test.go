//go:build !windows

package apiserver

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestFallbackRollbackRejectsLinkedPrivateFile(t *testing.T) {
	for _, link := range []string{"symlink", "hardlink"} {
		t.Run(link, func(t *testing.T) {
			root := t.TempDir()
			private := filepath.Join(root, "management.token")
			if err := os.WriteFile(private, []byte("synthetic-private-token"), 0o600); err != nil {
				t.Fatal(err)
			}
			target := filepath.Join(root, "config.20261006-123000.yaml")
			makeLink := os.Symlink
			if link == "hardlink" {
				makeLink = os.Link
			}
			if err := makeLink(private, target); err != nil {
				t.Skipf("filesystem cannot create fixture: %v", err)
			}
			recorder := httptest.NewRecorder()
			server := &ClassificationAPIServer{}
			data, _, ok := server.loadRollbackBackup(recorder, target, "20261006-123000")
			if ok || len(data) != 0 || recorder.Code != http.StatusNotFound {
				t.Fatalf("linked rollback was read: ok=%v bytes=%d status=%d", ok, len(data), recorder.Code)
			}
		})
	}
}

func TestFallbackVersionSourceRejectsLinkedMetadata(t *testing.T) {
	root := t.TempDir()
	private := filepath.Join(root, "private-source")
	if err := os.WriteFile(private, []byte("api"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(private, configVersionSourcePath(root, "20261006-123000")); err != nil {
		t.Skipf("filesystem cannot create fixture: %v", err)
	}
	if got := readConfigVersionSource(root, "20261006-123000"); got != configVersionSourceUnknown {
		t.Fatalf("linked source metadata was read: %q", got)
	}
}
