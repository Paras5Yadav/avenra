// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package main

import (
	"avenra.local/accounting/deployments/pulumi/pkg"
	"avenra.local/accounting/deployments/pulumi/pkg/config"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"

	_ "avenra.local/accounting/deployments/pulumi/pkg/exporters/clickhouse"
)

func main() {
	pulumi.Run(func(ctx *pulumi.Context) error {
		cfg, err := config.Load(ctx)
		if err != nil {
			return err
		}

		cmp, err := ledger.NewComponent(ctx, ctx.Stack(), cfg.ToInput())
		if err != nil {
			return err
		}

		ctx.Export("namespace", cmp.Namespace.Metadata.Name())
		ctx.Export("api-deployment", cmp.API.Deployment.Metadata.Name())
		ctx.Export("api-service", cmp.API.Service.Metadata.Name().Elem())
		ctx.Export("worker-deployment", cmp.Worker.Deployment.Metadata.Name())
		ctx.Export("postgres-service", pulumi.Sprintf("%s", cmp.Storage.Service.Metadata.Name().Elem()))
		ctx.Export("postgres-username", cmp.Storage.DatabaseComponent.GetUsername())
		ctx.Export("postgres-password", cmp.Storage.DatabaseComponent.GetPassword())

		return err
	})
}
