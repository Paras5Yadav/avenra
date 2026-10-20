// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package testserver

import (
	"context"
	"avenra.local/accounting/internal/replication/drivers"
)

type Driver interface {
	Config() map[string]any
	Name() string
	ReadMessages(ctx context.Context) ([]drivers.LogWithLedger, error)
	Clear(ctx context.Context) error
}
