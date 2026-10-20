// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package testserver

import (
	"github.com/formancehq/go-libs/v3/testing/deferred"
	"github.com/formancehq/go-libs/v3/testing/testservice"
	ledgerclient "avenra.local/accounting/pkg/client"
)

func Client(srv *testservice.Service) *ledgerclient.Avenra {
	return ledgerclient.New(
		ledgerclient.WithServerURL(testservice.GetServerURL(srv).String()),
	)
}

func DeferClient(srv *deferred.Deferred[*testservice.Service]) *deferred.Deferred[*ledgerclient.Avenra] {
	return deferred.Map(srv, Client)
}
