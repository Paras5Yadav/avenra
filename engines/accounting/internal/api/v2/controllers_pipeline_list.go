// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package v2

import (
	systemcontroller "avenra.local/accounting/internal/controller/system"
	"net/http"

	"github.com/formancehq/go-libs/v3/api"
)

func listPipelines(systemController systemcontroller.Controller) func(w http.ResponseWriter, r *http.Request) {
	return func(w http.ResponseWriter, r *http.Request) {
		pipelines, err := systemController.ListPipelines(r.Context())
		if err != nil {
			api.InternalServerError(w, r, err)
			return
		}

		api.RenderCursor(w, *pipelines)
	}

}
