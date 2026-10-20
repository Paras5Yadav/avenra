// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package v2

import (
	"errors"
	"github.com/formancehq/go-libs/v3/api"
	ledger "avenra.local/accounting/internal"
	"avenra.local/accounting/internal/api/common"
	systemcontroller "avenra.local/accounting/internal/controller/system"
	"github.com/go-chi/chi/v5"
	"net/http"
)

func updateExporter(systemController systemcontroller.Controller) func(w http.ResponseWriter, r *http.Request) {
	return func(w http.ResponseWriter, r *http.Request) {
		exporterID := chi.URLParam(r, "exporterID")
		common.WithBody[ledger.ExporterConfiguration](w, r, func(req ledger.ExporterConfiguration) {
			err := systemController.UpdateExporter(r.Context(), exporterID, req)
			if err != nil {
				switch {
				case errors.Is(err, systemcontroller.ErrInvalidDriverConfiguration{}):
					api.BadRequest(w, "VALIDATION", err)
				case errors.Is(err, systemcontroller.ErrExporterNotFound("")):
					api.NotFound(w, err)
				default:
					api.InternalServerError(w, r, err)
				}
				return
			}

			w.WriteHeader(http.StatusNoContent)
		})
	}
}

