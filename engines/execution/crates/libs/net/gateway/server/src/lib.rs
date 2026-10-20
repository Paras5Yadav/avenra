// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
pub mod certificates;
pub mod control;
pub mod debug;
pub mod dns;
pub mod egress;
pub mod ingress;
pub mod inherited;
pub mod known_egress;
pub mod reserved_dns;
pub mod resource_budget;
pub mod route_verification;
mod run;
pub mod sniff;
pub mod state;
pub mod stats;
pub mod tcp_proxy;
pub mod tls_interception;
pub mod tls_sni;
pub mod udp_proxy;

pub use avenra_runtime_net_gateway_client::connect;
pub use avenra_runtime_net_gateway_client::connect_path;
pub use avenra_runtime_net_gateway_client::control_socket_path;
pub use avenra_runtime_net_gateway_client::orig_dst;
pub use avenra_runtime_net_gateway_client::proto;
pub use avenra_runtime_net_gateway_client::{TCP_PROXY_PORT, UDP_MAPPING_TIMEOUT_SECS, UDP_PROXY_PORT};
pub use run::{InitialPolicyState, NetGatewayRunConfig, RunNetGatewayError, SERVICE_NAME, run};
pub use state::{HydrateNetworkStateError, NetworkPolicyFromHostControlError};
