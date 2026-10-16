//go:build !windows

package historylock

import (
	"fmt"
	"os"
	"strconv"
	"strings"

	"golang.org/x/sys/unix"
)

// SharedGroupEnv opts a launcher into owner-and-private-group history access.
// Without it, standalone histories retain their owner-only 0700/0600 policy.
const SharedGroupEnv = "VLLM_SR_CONFIG_HISTORY_GID"

func sharedGroup() (int, bool, error) {
	raw := strings.TrimSpace(os.Getenv(SharedGroupEnv))
	if raw == "" {
		return 0, false, nil
	}
	gid, err := strconv.ParseUint(raw, 10, 32)
	if err != nil || gid == 0 {
		return 0, false, fmt.Errorf("%s must name a non-root private group", SharedGroupEnv)
	}
	return int(gid), true, nil
}

// PrepareDirectory pins the directory before inspecting or changing modes.
// A shared directory must already belong to the explicitly selected group;
// ownership is prepared by the launcher, never changed by the runtime.
func PrepareDirectory(dir string) error {
	gid, shared, err := sharedGroup()
	if err != nil {
		return err
	}
	if mkdirErr := os.MkdirAll(dir, 0o700); mkdirErr != nil {
		return mkdirErr
	}
	fd, err := unix.Open(dir, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		return err
	}
	defer func() { _ = unix.Close(fd) }()
	var info unix.Stat_t
	if err := unix.Fstat(fd, &info); err != nil {
		return err
	}
	mode := uint64(0o700)
	if shared {
		if int(info.Gid) != gid {
			return fmt.Errorf("history directory does not belong to its configured private group")
		}
		mode = unix.S_ISGID | 0o770
	}
	if uint64(info.Mode)&0o7777 == mode {
		return nil
	}
	return unix.Fchmod(fd, uint32(mode))
}

// FileMode is the permission policy of a history's lock and snapshots.
func FileMode(dir string) (os.FileMode, error) {
	gid, shared, err := sharedGroup()
	if err != nil || !shared {
		return 0o600, err
	}
	fd, err := unix.Open(dir, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		return 0, err
	}
	defer func() { _ = unix.Close(fd) }()
	var info unix.Stat_t
	if err := unix.Fstat(fd, &info); err != nil {
		return 0, err
	}
	if int(info.Gid) != gid || info.Mode&0o7777 != unix.S_ISGID|0o770 {
		return 0, fmt.Errorf("shared history directory is not private to its configured group")
	}
	return 0o660, nil
}
