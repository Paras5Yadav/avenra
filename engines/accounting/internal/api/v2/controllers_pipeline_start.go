// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package v2

import (
	ledger "avenra.local/accounting/internal"
	ledgercontroller "avenra.local/accounting/internal/controller/ledger"
	systemcontroller "avenra.local/accounting/internal/controller/system"
	"net/http"

	"github.com/formancehq/go-libs/v3/api"
	"github.com/pkg/errors"
)

func startPipeline(systemController systemcontroller.Controller) func(w http.ResponseWriter, r *http.Request) {
	return func(w http.ResponseWriter, r *http.Request) {
		if err := systemController.StartPipeline(r.Context(), getPipelineID(r)); err != nil {
			switch {
			case errors.Is(err, ledger.ErrPipelineNotFound("")):
				api.NotFound(w, err)
			case errors.Is(err, ledger.ErrAlreadyStarted("")) ||
				errors.Is(err, ledgercontroller.ErrInUsePipeline("")):
				api.BadRequest(w, "VALIDATION", err)
			default:
				api.InternalServerError(w, r, err)
			}
			return
		}

		w.WriteHeader(http.StatusAccepted)
	}

}
