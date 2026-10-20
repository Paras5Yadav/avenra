// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
use std::fmt;
use std::path::{Path, PathBuf};

#[cfg(unix)]
use std::os::unix::net::SocketAddr as UnixSocketAddr;

/// Error returned when a Unix socket path is not usable as a pathname socket.
///
/// ```
/// # use avenra_runtime_common_svc::types::UnixSocketPath;
/// let err = UnixSocketPath::parse("relative.sock").unwrap_err();
/// assert_eq!(
///     err.to_string(),
///     "unix socket path must be absolute, non-empty, NUL-free, and fit platform limits"
/// );
/// ```
#[derive(Debug, Clone, Eq, PartialEq, thiserror::Error)]
#[error("unix socket path must be absolute, non-empty, NUL-free, and fit platform limits")]
pub struct UnixSocketPathParseError;

/// Validated absolute Unix domain socket path.
///
/// ```
/// # use avenra_runtime_common_svc::types::UnixSocketPath;
/// let path = UnixSocketPath::parse("/run/avenra/node.sock").unwrap();
/// assert_eq!(path.as_path().to_str(), Some("/run/avenra/node.sock"));
/// ```
#[derive(Clone, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
pub struct UnixSocketPath(PathBuf);

impl UnixSocketPath {
    /// Parses an absolute Unix socket path that fits the platform `sun_path`.
    ///
    /// ```
    /// # use avenra_runtime_common_svc::types::UnixSocketPath;
    /// assert!(UnixSocketPath::parse("/run/avenra/api.sock").is_ok());
    /// assert!(UnixSocketPath::parse("api.sock").is_err());
    /// assert!(UnixSocketPath::parse(format!("/tmp/{}.sock", "a".repeat(120))).is_err());
    /// ```
    pub fn parse(value: impl Into<PathBuf>) -> Result<Self, UnixSocketPathParseError> {
        let value = value.into();
        if value.as_os_str().is_empty() || !value.is_absolute() {
            return Err(UnixSocketPathParseError);
        }
        #[cfg(unix)]
        UnixSocketAddr::from_pathname(&value).map_err(|_| UnixSocketPathParseError)?;
        Ok(Self(value))
    }

    /// Returns the validated socket path.
    ///
    /// ```
    /// # use avenra_runtime_common_svc::types::UnixSocketPath;
    /// let path = UnixSocketPath::parse("/run/avenra/bridge.sock").unwrap();
    /// assert_eq!(path.as_path().to_str(), Some("/run/avenra/bridge.sock"));
    /// ```
    pub fn as_path(&self) -> &Path {
        &self.0
    }
}

macro_rules! socket_path_type {
    ($(#[$meta:meta])* $name:ident) => {
        $(#[$meta])*
        #[derive(Clone, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
        pub struct $name(UnixSocketPath);

        impl $name {
            #[doc = concat!("Builds a `", stringify!($name), "` from a validated socket path.")]
            #[doc = ""]
            #[doc = "```"]
            #[doc = concat!(
                "# use avenra_runtime_common_svc::types::{",
                stringify!($name),
                ", UnixSocketPath};"
            )]
            #[doc = "let path = UnixSocketPath::parse(\"/run/avenra/service.sock\").unwrap();"]
            #[doc = concat!("let typed = ", stringify!($name), "::new(path);")]
            #[doc = "assert_eq!(typed.socket_path().as_path().to_str(), Some(\"/run/avenra/service.sock\"));"]
            #[doc = "```"]
            pub const fn new(value: UnixSocketPath) -> Self {
                Self(value)
            }

            #[doc = concat!("Returns the underlying `UnixSocketPath` for this `", stringify!($name), "`.")]
            #[doc = ""]
            #[doc = "```"]
            #[doc = concat!(
                "# use avenra_runtime_common_svc::types::{",
                stringify!($name),
                ", UnixSocketPath};"
            )]
            #[doc = "let path = UnixSocketPath::parse(\"/run/avenra/service.sock\").unwrap();"]
            #[doc = concat!("let typed = ", stringify!($name), "::new(path);")]
            #[doc = "assert!(typed.socket_path().as_path().is_absolute());"]
            #[doc = "```"]
            pub const fn socket_path(&self) -> &UnixSocketPath {
                &self.0
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                self.0.as_path().display().fmt(f)
            }
        }
    };
}

socket_path_type!(
    /// Unix socket path used to expose a vsock bridge over UDS.
    ///
    /// ```
    /// # use avenra_runtime_common_svc::types::{UnixSocketPath, VsockUdsPath};
    /// let path = UnixSocketPath::parse("/run/avenra/vsock.sock").unwrap();
    /// let uds = VsockUdsPath::new(path);
    /// assert!(uds.socket_path().as_path().is_absolute());
    /// ```
    VsockUdsPath
);
socket_path_type!(
    /// Unix socket path for the avenrad-host service.
    ///
    /// ```
    /// # use avenra_runtime_common_svc::types::{SpacedHostSocketPath, UnixSocketPath};
    /// let path = UnixSocketPath::parse("/run/avenra/avenrad-host.sock").unwrap();
    /// let socket = SpacedHostSocketPath::new(path);
    /// assert!(socket.socket_path().as_path().is_absolute());
    /// ```
    SpacedHostSocketPath
);
socket_path_type!(
    /// Unix socket path for the network gateway control service.
    ///
    /// ```
    /// # use avenra_runtime_common_svc::types::{NetGatewayControlSocketPath, UnixSocketPath};
    /// let path = UnixSocketPath::parse("/run/avenra/net-gateway.sock").unwrap();
    /// let socket = NetGatewayControlSocketPath::new(path);
    /// assert!(socket.socket_path().as_path().is_absolute());
    /// ```
    NetGatewayControlSocketPath
);
socket_path_type!(
    /// Unix socket path for the bridge control service.
    ///
    /// ```
    /// # use avenra_runtime_common_svc::types::{BridgeControlSocketPath, UnixSocketPath};
    /// let path = UnixSocketPath::parse("/run/avenra/bridge.sock").unwrap();
    /// let socket = BridgeControlSocketPath::new(path);
    /// assert!(socket.socket_path().as_path().is_absolute());
    /// ```
    BridgeControlSocketPath
);

#[cfg(test)]
mod tests;
