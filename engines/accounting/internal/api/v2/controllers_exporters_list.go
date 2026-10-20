// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package v2

import (
	"github.com/formancehq/go-libs/v3/api"
	systemcontroller "avenra.local/accounting/internal/controller/system"
	"net/http"
)

func listExporters(systemController systemcontroller.Controller) func(w http.ResponseWriter, r *http.Request) {
	return func(w http.ResponseWriter, r *http.Request) {
		exporters, err := systemController.ListExporters(r.Context())
		if err != nil {
			api.InternalServerError(w, r, err)
			return
		}

		api.RenderCursor(w, *exporters)
	}
}
