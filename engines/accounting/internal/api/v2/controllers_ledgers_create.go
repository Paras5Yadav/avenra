// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package v2

import (
	"encoding/json"
	"avenra.local/accounting/internal/api/common"
	"io"
	"net/http"

	systemcontroller "avenra.local/accounting/internal/controller/system"

	ledger "avenra.local/accounting/internal"

	"errors"
	"github.com/formancehq/go-libs/v3/api"
	"github.com/go-chi/chi/v5"
)

func createLedger(systemController systemcontroller.Controller) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		configuration := ledger.Configuration{}
		data, err := io.ReadAll(r.Body)
		if err != nil && !errors.Is(err, io.EOF) {
			common.InternalServerError(w, r, err)
			return
		}

		if len(data) > 0 {
			if err := json.Unmarshal(data, &configuration); err != nil {
				api.BadRequest(w, common.ErrValidation, err)
				return
			}
		}

		if err := systemController.CreateLedger(r.Context(), chi.URLParam(r, "ledger"), configuration); err != nil {
			switch {
			case errors.Is(err, systemcontroller.ErrInvalidLedgerConfiguration{}) ||
				errors.Is(err, ledger.ErrInvalidLedgerName{}) ||
				errors.Is(err, ledger.ErrInvalidBucketName{}) ||
				errors.Is(err, systemcontroller.ErrExperimentalFeaturesDisabled):
				api.BadRequest(w, common.ErrValidation, err)
			case errors.Is(err, systemcontroller.ErrBucketOutdated):
				api.BadRequest(w, common.ErrOutdatedSchema, err)
			case errors.Is(err, systemcontroller.ErrLedgerAlreadyExists):
				api.BadRequest(w, common.ErrLedgerAlreadyExists, err)
			default:
				common.HandleCommonErrors(w, r, err)
			}
			return
		}
		api.NoContent(w)
	}
}
