//go:build !windows

package historylock

import (
	"os"

	"golang.org/x/sys/unix"
)

func openSnapshot(path string) (*os.File, error) {
	fd, err := unix.Open(path, unix.O_RDONLY|unix.O_NOFOLLOW|unix.O_CLOEXEC|unix.O_NONBLOCK, 0)
	if err != nil {
		return nil, err
	}
	return os.NewFile(uintptr(fd), path), nil
}

func snapshotHasSingleLink(file *os.File) (bool, error) {
	var info unix.Stat_t
	if err := unix.Fstat(int(file.Fd()), &info); err != nil {
		return false, err
	}
	return info.Nlink == 1, nil
}
