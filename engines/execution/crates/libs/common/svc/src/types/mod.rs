// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
//! Service boundary types shared by Avenra daemons.
//!
//! These types keep transport-specific concerns out of core domain nouns. Use
//! them when a value is meaningful only at service boundaries: request auth,
//! endpoint discovery, gRPC/HTTP error conversion, Kubernetes labels, or local
//! Unix socket addresses.
//!
//! ```
//! # use avenra_runtime_common_svc::types::*;
//! let name = ServiceName::parse("avenra-sbxmgr").unwrap();
//! assert_eq!(name.as_str(), "avenra-sbxmgr");
//! ```

pub mod auth;
pub mod auto_lifecycle;
pub mod endpoint;
pub mod errors;
pub mod grpc;
pub mod http;
pub mod k8s;
pub mod reconcile;
pub mod sockets;

pub use auth::*;
pub use auto_lifecycle::*;
pub use endpoint::*;
pub use errors::*;
pub use grpc::*;
pub use http::*;
pub use reconcile::*;
pub use sockets::*;
