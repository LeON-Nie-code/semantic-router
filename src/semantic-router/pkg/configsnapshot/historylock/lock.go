// Package historylock is the lock every writer of a configuration history
// directory holds while it writes: the Router and the Dashboard, in one
// process or several. It depends on nothing else in the Router.
package historylock

import (
	"errors"
	"fmt"
	"path/filepath"
	"time"

	"golang.org/x/sys/unix"
)

// name is the file a writer locks.
const name = ".lock"

// wait bounds how long a writer waits for another to finish.
const wait = 10 * time.Second

// Lock holds the lock of the history directory dir, which it creates private
// to its owner, until unlock is called.
func Lock(dir string) (unlock func(), err error) {
	if err = PrepareDirectory(dir); err != nil {
		return nil, err
	}
	mode, err := FileMode(dir)
	if err != nil {
		return nil, err
	}
	flags := unix.O_RDWR | unix.O_CLOEXEC | unix.O_NOFOLLOW
	fd, err := unix.Open(filepath.Join(dir, name), flags|unix.O_CREAT|unix.O_EXCL, uint32(mode))
	created := err == nil
	if errors.Is(err, unix.EEXIST) {
		fd, err = unix.Open(filepath.Join(dir, name), flags, 0)
	}
	if err != nil {
		return nil, fmt.Errorf("open the lock of %s: %w", dir, err)
	}
	var info unix.Stat_t
	if err = unix.Fstat(fd, &info); err == nil {
		if info.Mode&unix.S_IFMT != unix.S_IFREG || info.Nlink != 1 {
			err = errors.New("history lock must be a private regular file")
		} else if created {
			err = unix.Fchmod(fd, uint32(mode))
		} else if info.Mode&0o777 != uint32(mode) {
			err = errors.New("history lock permissions do not match its directory policy")
		}
	}
	if err != nil {
		_ = unix.Close(fd)
		return nil, err
	}
	deadline := time.Now().Add(wait)
	for {
		err = unix.Flock(fd, unix.LOCK_EX|unix.LOCK_NB)
		if !errors.Is(err, unix.EWOULDBLOCK) || time.Now().After(deadline) {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if err != nil {
		_ = unix.Close(fd)
		return nil, fmt.Errorf("lock %s: %w", dir, err)
	}
	return func() {
		_ = unix.Flock(fd, unix.LOCK_UN)
		_ = unix.Close(fd)
	}, nil
}
