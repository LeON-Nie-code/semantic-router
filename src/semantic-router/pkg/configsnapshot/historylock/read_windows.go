//go:build windows

package historylock

import (
	"os"

	"golang.org/x/sys/windows"
)

func openSnapshot(path string) (*os.File, error) {
	return os.Open(path)
}

func snapshotHasSingleLink(file *os.File) (bool, error) {
	var info windows.ByHandleFileInformation
	if err := windows.GetFileInformationByHandle(windows.Handle(file.Fd()), &info); err != nil {
		return false, err
	}
	return info.NumberOfLinks == 1, nil
}
