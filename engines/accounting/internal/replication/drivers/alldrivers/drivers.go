// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package alldrivers

import (
	"avenra.local/accounting/internal/replication/drivers"
	"avenra.local/accounting/internal/replication/drivers/clickhouse"
	"avenra.local/accounting/internal/replication/drivers/elasticsearch"
	"avenra.local/accounting/internal/replication/drivers/http"
	"avenra.local/accounting/internal/replication/drivers/noop"
	"avenra.local/accounting/internal/replication/drivers/stdout"
)

func Register(driversRegistry *drivers.Registry) {
	driversRegistry.RegisterDriver("elasticsearch", elasticsearch.NewDriver)
	driversRegistry.RegisterDriver("clickhouse", clickhouse.NewDriver)
	driversRegistry.RegisterDriver("stdout", stdout.NewDriver)
	driversRegistry.RegisterDriver("http", http.NewDriver)
	driversRegistry.RegisterDriver("noop", noop.NewDriver)
}
