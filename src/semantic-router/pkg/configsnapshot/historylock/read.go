package historylock

import (
	"fmt"
	"io"
	"os"
)

// ReadSnapshot reads only a pinned, single-link regular file. History writers
// must not redirect a privileged reader to credentials outside the history.
func ReadSnapshot(path string) ([]byte, error) {
	before, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	if !before.Mode().IsRegular() {
		return nil, fmt.Errorf("history snapshot %s is not a regular file", path)
	}
	file, err := openSnapshot(path)
	if err != nil {
		return nil, err
	}
	defer func() { _ = file.Close() }()
	after, err := file.Stat()
	if err != nil {
		return nil, err
	}
	single, err := snapshotHasSingleLink(file)
	if err != nil {
		return nil, err
	}
	if !after.Mode().IsRegular() || !os.SameFile(before, after) || !single {
		return nil, fmt.Errorf("history snapshot %s changed identity or has multiple links", path)
	}
	return io.ReadAll(file)
}
