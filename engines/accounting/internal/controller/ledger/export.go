// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package ledger

import (
	"context"

	ledger "avenra.local/accounting/internal"
)

type ExportWriter interface {
	Write(ctx context.Context, log ledger.Log) error
}

type ExportWriterFn func(ctx context.Context, log ledger.Log) error

func (fn ExportWriterFn) Write(ctx context.Context, log ledger.Log) error {
	return fn(ctx, log)
}
