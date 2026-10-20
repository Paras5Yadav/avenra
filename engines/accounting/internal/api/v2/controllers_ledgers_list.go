// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package v2

import (
	"avenra.local/accounting/internal/api/common"
	systemstore "avenra.local/accounting/internal/storage/system"
	"net/http"

	"github.com/formancehq/go-libs/v3/api"
	"github.com/formancehq/go-libs/v3/bun/bunpaginate"
	"avenra.local/accounting/internal/controller/system"
)

func listLedgers(b system.Controller, paginationConfig common.PaginationConfig) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {

		rq, err := getPaginatedQuery[systemstore.ListLedgersQueryPayload](r, paginationConfig, "id", bunpaginate.OrderAsc)
		if err != nil {
			api.BadRequest(w, common.ErrValidation, err)
			return
		}

		ledgers, err := b.ListLedgers(r.Context(), rq)
		if err != nil {
			common.HandleCommonPaginationErrors(w, r, err)
			return
		}

		api.RenderCursor(w, *ledgers)
	}
}
