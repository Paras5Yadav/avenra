// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package v1

import (
	_ "embed"
	"avenra.local/accounting/internal/api/common"
	storagecommon "avenra.local/accounting/internal/storage/common"
	systemstore "avenra.local/accounting/internal/storage/system"
	"net/http"

	"avenra.local/accounting/internal/controller/system"

	"github.com/formancehq/go-libs/v3/bun/bunpaginate"
	ledger "avenra.local/accounting/internal"

	"github.com/formancehq/go-libs/v3/collectionutils"

	"github.com/formancehq/go-libs/v3/api"
)

type ConfigInfo struct {
	Server               string        `json:"server"`
	Version              string        `json:"version"`
	Config               *LedgerConfig `json:"config"`
	ExperimentalFeatures []string      `json:"experimentalFeatures"`
}

type LedgerConfig struct {
	LedgerStorage *LedgerStorage `json:"storage"`
}

type LedgerStorage struct {
	Driver  string   `json:"driver"`
	Ledgers []string `json:"ledgers"`
}

func GetInfo(systemController system.Controller, version string, experimentalFeatures []string) func(w http.ResponseWriter, r *http.Request) {
	return func(w http.ResponseWriter, r *http.Request) {

		ledgerNames := make([]string, 0)
		if err := storagecommon.Iterate(r.Context(), storagecommon.InitialPaginatedQuery[systemstore.ListLedgersQueryPayload]{
			PageSize: 100,
		},
			systemController.ListLedgers,
			func(cursor *bunpaginate.Cursor[ledger.Ledger]) error {
				ledgerNames = append(ledgerNames, collectionutils.Map(cursor.Data, func(from ledger.Ledger) string {
					return from.Name
				})...)
				return nil
			},
		); err != nil {
			common.HandleCommonErrors(w, r, err)
			return
		}

		api.Ok(w, ConfigInfo{
			Server:  "ledger",
			Version: version,
			Config: &LedgerConfig{
				LedgerStorage: &LedgerStorage{
					Driver:  "postgres",
					Ledgers: ledgerNames,
				},
			},
			ExperimentalFeatures: experimentalFeatures,
		})
	}
}
