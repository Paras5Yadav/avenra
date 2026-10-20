// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package main

import (
	"encoding/json"
	"fmt"
	"avenra.local/accounting/deployments/pulumi/pkg/config"
	"github.com/invopop/jsonschema"
	"github.com/spf13/cobra"
	"os"
)

func main() {
	cmd := &cobra.Command{
		Use: "tools",
	}
	schema := &cobra.Command{
		Use:  "schema",
		RunE: printSchema,
	}
	cmd.AddCommand(schema)

	if err := cmd.Execute(); err != nil {
		_, _ = fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func printSchema(_ *cobra.Command, _ []string) error {
	reflector := jsonschema.Reflector{
		//DoNotReference:             true,
		RequiredFromJSONSchemaTags: true,
	}
	if err := reflector.AddGoComments("avenra.local/accounting/deployments/pulumi", "./pkg"); err != nil {
		return err
	}
	schema := reflector.Reflect(config.Config{})

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")

	return enc.Encode(schema)
}
