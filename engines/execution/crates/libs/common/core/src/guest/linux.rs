// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
//! Avenra Linux guest contracts.

/// Avenra guest Linux network contract.
pub mod network {
    pub const PRIMARY_INTERFACE: &str = "eth0";
    pub const IPV4_ADDR: &str = "169.254.0.21";
    pub const GATEWAY_IPV4_ADDR: &str = "169.254.0.22";
    pub const IPV4_NETMASK: &str = "255.255.255.252";
    pub const GATEWAY_IPV4_CIDR: &str = "169.254.0.22/30";
}

/// Avenra guest Linux filesystem paths.
pub mod paths {
    use std::path::PathBuf;

    pub const AvenraD_GUEST_SOCKET_DIR: &str = "/run";

    pub const AvenraD_GUEST_SOCKET_FILE: &str = "avenrad.sock";
    pub const AvenraD_BRIDGE_GUEST_SOCKET_FILE: &str = "avenrad-bridge-guest.sock";
    pub const AvenraD_BRIDGE_HOST_SOCKET_FILE: &str = "avenrad-bridge-host.sock";

    /// Returns the default guest-side UDS path for a socket file.
    ///
    /// # Examples
    ///
    /// ```rust
    /// # use avenra_runtime_common_core::guest::linux::paths::default_guest_uds_path;
    /// assert!(default_guest_uds_path("avenrad.sock").ends_with("avenrad.sock"));
    /// ```
    pub fn default_guest_uds_path(socket_file: &str) -> PathBuf {
        default_uds_path(AvenraD_GUEST_SOCKET_DIR, socket_file)
    }

    /// Returns a platform-appropriate default UDS path.
    ///
    /// # Examples
    ///
    /// ```rust
    /// # use avenra_runtime_common_core::guest::linux::paths::default_uds_path;
    /// assert!(default_uds_path("/run", "svc.sock").ends_with("svc.sock"));
    /// ```
    pub fn default_uds_path(linux_socket_dir: &str, socket_file: &str) -> PathBuf {
        if cfg!(target_os = "linux") {
            PathBuf::from(linux_socket_dir)
        } else {
            std::env::temp_dir()
        }
        .join(socket_file)
    }
}
