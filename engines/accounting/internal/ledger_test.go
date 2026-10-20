// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package ledger

import (
	"avenra.local/accounting/pkg/features"
	"github.com/stretchr/testify/require"
	"testing"
)

func TestFeatures(t *testing.T) {
	f := features.MinimalFeatureSet.With(features.FeatureMovesHistory, "DISABLED")
	require.Equal(t, "DISABLED", f[features.FeatureMovesHistory])
	require.Equal(t, "AMH=DISABLED,HL=DISABLED,MH=DISABLED,MHPCEV=DISABLED,TMH=DISABLED", f.String())
}
