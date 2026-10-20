// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package system

import (
	"context"
	ledgercontroller "avenra.local/accounting/internal/controller/ledger"
	"avenra.local/accounting/internal/storage/common"
	"avenra.local/accounting/internal/storage/system"

	"github.com/formancehq/go-libs/v3/metadata"
	ledger "avenra.local/accounting/internal"
)

type Store interface {
	GetLedger(ctx context.Context, name string) (*ledger.Ledger, error)
	Ledgers() common.PaginatedResource[ledger.Ledger, system.ListLedgersQueryPayload]
	UpdateLedgerMetadata(ctx context.Context, name string, m metadata.Metadata) error
	DeleteLedgerMetadata(ctx context.Context, param string, key string) error
}

type Driver interface {
	OpenLedger(context.Context, string) (ledgercontroller.Store, *ledger.Ledger, error)
	CreateLedger(context.Context, *ledger.Ledger) error
	GetSystemStore() Store
}
