// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package v2

import (
	"errors"
	"github.com/formancehq/go-libs/v3/api"
	ledger "avenra.local/accounting/internal"
	"avenra.local/accounting/internal/api/common"
	systemcontroller "avenra.local/accounting/internal/controller/system"
	"net/http"
)

func createExporter(systemController systemcontroller.Controller) func(w http.ResponseWriter, r *http.Request) {
	return func(w http.ResponseWriter, r *http.Request) {
		common.WithBody[ledger.ExporterConfiguration](w, r, func(req ledger.ExporterConfiguration) {
			exporter, err := systemController.CreateExporter(r.Context(), req)
			if err != nil {
				switch {
				case errors.Is(err, systemcontroller.ErrInvalidDriverConfiguration{}):
					api.BadRequest(w, "VALIDATION", err)
				default:
					api.InternalServerError(w, r, err)
				}
				return
			}

			api.Created(w, exporter)
		})
	}
}
