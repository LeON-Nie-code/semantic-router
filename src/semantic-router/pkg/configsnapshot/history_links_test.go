package configsnapshot

import (
	"os"
	"path/filepath"
	"testing"
)

func TestDirStoreRejectsLinkedRecordDocuments(t *testing.T) {
	for _, link := range []string{"symlink", "hardlink"} {
		t.Run(link, func(t *testing.T) {
			root := t.TempDir()
			history := filepath.Join(root, "history")
			if err := os.Mkdir(history, 0o700); err != nil {
				t.Fatal(err)
			}
			private := filepath.Join(root, "management.token")
			if err := os.WriteFile(private, []byte("synthetic-private-token"), 0o600); err != nil {
				t.Fatal(err)
			}
			target := filepath.Join(history, "config.20261006-123000.yaml")
			makeLink := os.Symlink
			if link == "hardlink" {
				makeLink = os.Link
			}
			if err := makeLink(private, target); err != nil {
				t.Skipf("filesystem cannot create fixture: %v", err)
			}
			records, err := NewDirStore(history).Load()
			if err == nil || len(records) != 0 {
				t.Fatalf("linked private file accepted as history: records=%d err=%v", len(records), err)
			}
		})
	}
}
