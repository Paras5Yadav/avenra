// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package v2

import (
	"github.com/go-chi/chi/v5"
	"net/http"

	"avenra.local/accounting/internal/api/common"

	"github.com/formancehq/go-libs/v3/api"
	"github.com/formancehq/go-libs/v3/metadata"
	systemcontroller "avenra.local/accounting/internal/controller/system"
)

func updateLedgerMetadata(systemController systemcontroller.Controller) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		common.WithBody(w, r, func(m metadata.Metadata) {
			if err := systemController.UpdateLedgerMetadata(r.Context(), chi.URLParam(r, "ledger"), m); err != nil {
				common.HandleCommonWriteErrors(w, r, err)
				return
			}

			api.NoContent(w)
		})
	}
}
