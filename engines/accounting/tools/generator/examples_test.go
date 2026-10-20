// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
//go:build it

package main_test

import (
	"embed"
	"avenra.local/accounting/pkg/generate"
	"github.com/stretchr/testify/require"
	"path/filepath"
	"testing"
)

//go:embed examples
var examples embed.FS

func TestGenerator(t *testing.T) {
	dirEntries, err := examples.ReadDir("examples")
	require.NoError(t, err)

	for _, entry := range dirEntries {
		example, err := examples.ReadFile(filepath.Join("examples", entry.Name()))
		require.NoError(t, err)

		generator, err := generate.NewGenerator(string(example))
		require.NoError(t, err)

		_, err = generator.Next(1)
		require.NoError(t, err)
	}
}
