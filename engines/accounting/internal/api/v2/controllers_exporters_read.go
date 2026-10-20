// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package v2

import (
	"errors"
	"github.com/formancehq/go-libs/v3/api"
	systemcontroller "avenra.local/accounting/internal/controller/system"
	"net/http"
)

func getExporter(systemController systemcontroller.Controller) func(w http.ResponseWriter, r *http.Request) {
	return func(w http.ResponseWriter, r *http.Request) {
		exporter, err := systemController.GetExporter(r.Context(), getExporterID(r))
		if err != nil {
			switch {
			case errors.Is(err, systemcontroller.ErrExporterNotFound("")):
				api.NotFound(w, err)
			default:
				api.InternalServerError(w, r, err)
			}
			return
		}

		api.Ok(w, exporter)
	}
}
