// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
package drivers

import (
	ledger "avenra.local/accounting/internal"
)

type LogWithLedger struct {
	ledger.Log
	Ledger string `json:"ledger"`
}

func NewLogWithLedger(ledger string, log ledger.Log) LogWithLedger {
	return LogWithLedger{
		Log:    log,
		Ledger: ledger,
	}
}
