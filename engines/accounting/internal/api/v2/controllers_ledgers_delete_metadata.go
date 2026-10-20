// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package v2

import (
	"net/http"

	"avenra.local/accounting/internal/api/common"

	"avenra.local/accounting/internal/controller/system"

	"github.com/formancehq/go-libs/v3/api"
	"github.com/go-chi/chi/v5"
)

func deleteLedgerMetadata(b system.Controller) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if err := b.DeleteLedgerMetadata(r.Context(), chi.URLParam(r, "ledger"), chi.URLParam(r, "key")); err != nil {
			common.HandleCommonWriteErrors(w, r, err)
			return
		}

		api.NoContent(w)
	}
}
