// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package v2

import (
	"fmt"
	storagecommon "avenra.local/accounting/internal/storage/common"
	ledgerstore "avenra.local/accounting/internal/storage/ledger"
	"net/http"

	"errors"
	"github.com/formancehq/go-libs/v3/api"
	"avenra.local/accounting/internal/api/common"
)

func countTransactions(w http.ResponseWriter, r *http.Request) {

	rq, err := getResourceQuery[any](r)
	if err != nil {
		api.BadRequest(w, common.ErrValidation, err)
		return
	}

	count, err := common.LedgerFromContext(r.Context()).CountTransactions(r.Context(), *rq)
	if err != nil {
		switch {
		case errors.Is(err, storagecommon.ErrInvalidQuery{}) || errors.Is(err, ledgerstore.ErrMissingFeature{}):
			api.BadRequest(w, common.ErrValidation, err)
		default:
			common.HandleCommonErrors(w, r, err)
		}
		return
	}

	w.Header().Set("Count", fmt.Sprint(count))
	api.NoContent(w)
}
