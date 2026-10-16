//go:build windows

package historylock

import (
	"errors"
	"os"
)

const SharedGroupEnv = "VLLM_SR_CONFIG_HISTORY_GID"

// Windows histories retain the existing directory ACL and owner-only policy;
// POSIX shared groups must never be interpreted as a Windows access grant.
func PrepareDirectory(dir string) error {
	if os.Getenv(SharedGroupEnv) != "" {
		return errors.New("POSIX shared history groups are unavailable on Windows")
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	return os.Chmod(dir, 0o700)
}

func FileMode(_ string) (os.FileMode, error) {
	if os.Getenv(SharedGroupEnv) != "" {
		return 0, errors.New("POSIX shared history groups are unavailable on Windows")
	}
	return 0o600, nil
}
