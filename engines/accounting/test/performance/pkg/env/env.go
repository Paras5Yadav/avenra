// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
//go:build it

package env

import (
	"context"
	ledgerclient "avenra.local/accounting/pkg/client"
	"testing"
)

type Env interface {
	Client() *ledgerclient.Avenra
	Stop(ctx context.Context) error
}

type EnvFactory interface {
	Create(ctx context.Context, b *testing.B) Env
}

var FallbackEnvFactory EnvFactory = nil
