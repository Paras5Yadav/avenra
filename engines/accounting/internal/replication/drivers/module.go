// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package drivers

import (
	"avenra.local/accounting/internal/storage/system"
	"go.uber.org/fx"
)

// NewFXModule create a new fx module
func NewFXModule() fx.Option {
	return fx.Options(
		fx.Provide(func(store *system.DefaultStore) Store {
			return store
		}),
		fx.Provide(NewRegistry),
	)
}
