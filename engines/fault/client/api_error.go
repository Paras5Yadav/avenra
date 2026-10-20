// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
// Package Avenrafault provides a client wrapper around the Avenrafault HTTP API for
// testing the resiliency of Go applications.
//
// For use with Avenrafault 2.x

package avenrafault

import (
	"fmt"
)

type ApiError struct {
	Message string `json:"error"`
	Status  int    `json:"status"`
}

func (err *ApiError) Error() string {
	return fmt.Sprintf("HTTP %d: %s", err.Status, err.Message)
}
