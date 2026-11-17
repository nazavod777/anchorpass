use std::fmt;

/// Stable error category. Error messages never contain request bodies or credentials.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ErrorKind {
    InvalidInput,
    InvalidProxy,
    UnsupportedProxy,
    ProxyConnectError,
    ProxyAuthError,
    TransportError,
    DeadlineExceeded,
    Cancelled,
    HttpStatusError,
    ProtocolError,
    ConfigUnavailable,
    ConfigInvalid,
    ConfigExpired,
    ConfigRollback,
    ResourceLimit,
    WorkerUnavailable,
    ClientClosed,
}

#[derive(Clone, Debug)]
pub struct Error {
    pub kind: ErrorKind,
    pub stage: &'static str,
    pub retryable: bool,
    message: &'static str,
}
impl Error {
    #[cfg(any(feature = "async", feature = "blocking"))]
    pub(crate) fn new(kind: ErrorKind, stage: &'static str, message: &'static str) -> Self {
        Self {
            kind,
            stage,
            retryable: false,
            message,
        }
    }
}
impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:?} at {}: {}", self.kind, self.stage, self.message)
    }
}
impl std::error::Error for Error {}
pub type Result<T> = std::result::Result<T, Error>;
