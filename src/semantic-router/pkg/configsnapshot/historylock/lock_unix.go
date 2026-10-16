//go:build !windows

package historylock

import (
	"errors"
	"fmt"
	"os"

	"golang.org/x/sys/unix"
)

// lockFile is an open lock file.
type lockFile int

// openLock opens the lock file, refusing a symbolic link at its path so a
// writer cannot be pointed at another file's lock.
func openLock(path string, mode os.FileMode) (lockFile, error) {
	flags := unix.O_RDWR | unix.O_CLOEXEC | unix.O_NOFOLLOW
	fd, err := unix.Open(path, flags|unix.O_CREAT|unix.O_EXCL, uint32(mode))
	created := err == nil
	if errors.Is(err, unix.EEXIST) {
		fd, err = unix.Open(path, flags, 0)
	}
	if err != nil {
		return lockFile(fd), err
	}
	var info unix.Stat_t
	if err = unix.Fstat(fd, &info); err == nil {
		switch {
		case info.Mode&unix.S_IFMT != unix.S_IFREG || info.Nlink != 1:
			err = errors.New("history lock must be a private regular file")
		case created:
			err = unix.Fchmod(fd, uint32(mode))
		case uint64(info.Mode)&0o777 != uint64(mode):
			err = fmt.Errorf("history lock permissions do not match its directory policy")
		}
	}
	if err != nil {
		_ = unix.Close(fd)
	}
	return lockFile(fd), err
}

func tryLock(file lockFile) error {
	err := unix.Flock(int(file), unix.LOCK_EX|unix.LOCK_NB)
	if errors.Is(err, unix.EWOULDBLOCK) {
		return errLocked
	}
	return err
}

func unlockLock(file lockFile) error {
	return unix.Flock(int(file), unix.LOCK_UN)
}

func closeLock(file lockFile) error {
	return unix.Close(int(file))
}
