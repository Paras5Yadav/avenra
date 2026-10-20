// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package testserver

import (
	"context"
	"github.com/formancehq/go-libs/v3/testing/testservice"
	"avenra.local/accounting/cmd"
	"strconv"
)

func LogsHashBlockMaxSizeInstrumentation(size int) testservice.InstrumentationFunc {
	return func(ctx context.Context, runConfiguration *testservice.RunConfiguration) error {
		runConfiguration.AppendArgs("--"+cmd.WorkerAsyncBlockHasherMaxBlockSizeFlag, strconv.Itoa(size))
		return nil
	}
}

func LogsHashBlockCRONSpecInstrumentation(spec string) testservice.InstrumentationFunc {
	return func(ctx context.Context, runConfiguration *testservice.RunConfiguration) error {
		runConfiguration.AppendArgs("--"+cmd.WorkerAsyncBlockHasherScheduleFlag, spec)
		return nil
	}
}
