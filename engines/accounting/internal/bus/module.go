// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package bus

import (
	"avenra.local/accounting/internal/controller/ledger"
	"go.uber.org/fx"
)

func NewFxModule() fx.Option {
	return fx.Options(
		fx.Provide(fx.Annotate(NewLedgerListener, fx.As(new(ledger.Listener)))),
	)
}
