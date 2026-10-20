// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package drivers

import (
	"context"
	ledger "avenra.local/accounting/internal"
)

//go:generate mockgen -source store.go -destination store_generated.go -package drivers . Store
type Store interface {
	GetExporter(ctx context.Context, id string) (*ledger.Exporter, error)
}
