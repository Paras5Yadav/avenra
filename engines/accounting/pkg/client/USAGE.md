<!-- Start SDK Example Usage [usage] -->
```go
package main

import (
	"context"
	"avenra.local/accounting/pkg/client"
	"avenra.local/accounting/pkg/client/models/components"
	"log"
	"os"
)

func main() {
	ctx := context.Background()

	s := client.New(
		client.WithSecurity(components.Security{
			ClientID:     client.String(os.Getenv("FORMANCE_CLIENT_ID")),
			ClientSecret: client.String(os.Getenv("FORMANCE_CLIENT_SECRET")),
		}),
	)

	res, err := s.Ledger.GetInfo(ctx)
	if err != nil {
		log.Fatal(err)
	}
	if res.V2ConfigInfoResponse != nil {
		// handle response
	}
}

```
<!-- End SDK Example Usage [usage] -->