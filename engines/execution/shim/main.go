// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package main

import (
	"context"

	"github.com/containerd/containerd/v2/pkg/shim"
)

func main() {
	shim.Run(context.Background(), NewManager("io.containerd.avenra.v2"))
}
