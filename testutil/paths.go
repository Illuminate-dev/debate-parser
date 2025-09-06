// Package testutil contains utils for testing
package testutil

import (
	"os"
	"path/filepath"
	"runtime"
)

func ProjectRoot() string {
	_, filename, _, _ := runtime.Caller(0)
	dir := filepath.Dir(filename)

	for {
		if _, err := os.Stat(filepath.Join(dir, "go.mod")); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			panic("could not find project root")
		}
		dir = parent
	}
}

func TestDataPath(filename string) string {
	return filepath.Join(ProjectRoot(), "testdata", filename)
}
