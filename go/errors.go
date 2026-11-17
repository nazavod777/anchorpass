// Package anchorpass implements the recaptcha-v3/1 HTTP contract.
// A returned token does not establish acceptance or a score at the target site.
package anchorpass

import (
	"context"
	"errors"
)

// Code is a stable, language-independent error category.
type Code string

const (
	InvalidInput      Code = "InvalidInput"
	InvalidProxy      Code = "InvalidProxy"
	UnsupportedProxy  Code = "UnsupportedProxy"
	ProxyConnectError Code = "ProxyConnectError"
	ProxyAuthError    Code = "ProxyAuthError"
	TransportError    Code = "TransportError"
	DeadlineExceeded  Code = "DeadlineExceeded"
	Cancelled         Code = "Cancelled"
	HTTPStatusError   Code = "HttpStatusError"
	ProtocolError     Code = "ProtocolError"
	ConfigUnavailable Code = "ConfigUnavailable"
	ConfigInvalid     Code = "ConfigInvalid"
	ConfigExpired     Code = "ConfigExpired"
	ConfigRollback    Code = "ConfigRollback"
	ResourceLimit     Code = "ResourceLimit"
	ClientClosed      Code = "ClientClosed"
)

// Error deliberately excludes URLs, credentials, request bodies, and tokens.
type Error struct {
	Code      Code
	Stage     string
	Retryable bool
	cause     error
}

func (e *Error) Error() string              { return "anchorpass: " + e.Stage + ": " + string(e.Code) }
func (e *Error) Unwrap() error              { return e.cause }
func failure(code Code, stage string) error { return &Error{Code: code, Stage: stage} }
func contextError(ctx context.Context, stage string) error {
	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		return &Error{Code: DeadlineExceeded, Stage: stage, cause: context.DeadlineExceeded}
	}
	return &Error{Code: Cancelled, Stage: stage, cause: context.Canceled}
}
