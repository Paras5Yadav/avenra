// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package v1

import (
	storagecommon "avenra.local/accounting/internal/storage/common"
	"net/http"

	"github.com/formancehq/go-libs/v3/api"
	"github.com/formancehq/go-libs/v3/bun/bunpaginate"
	"avenra.local/accounting/internal/api/common"
)

func listTransactions(w http.ResponseWriter, r *http.Request) {
	l := common.LedgerFromContext(r.Context())

	paginatedQuery, err := getPaginatedQuery[any](
		r,
		"id",
		bunpaginate.OrderDesc,
		func(resourceQuery *storagecommon.ResourceQuery[any]) error {
			resourceQuery.Expand = append(resourceQuery.Expand, "volumes")
			resourceQuery.Builder = buildGetTransactionsQuery(r)
			return nil
		},
	)
	if err != nil {
		api.BadRequest(w, common.ErrValidation, err)
		return
	}

	cursor, err := l.ListTransactions(r.Context(), paginatedQuery)
	if err != nil {
		common.HandleCommonErrors(w, r, err)
		return
	}

	api.RenderCursor(w, *bunpaginate.MapCursor(cursor, mapTransactionToV1))
}
