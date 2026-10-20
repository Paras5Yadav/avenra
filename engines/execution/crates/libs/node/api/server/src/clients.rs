// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
use avenra_runtime_build_mgr_client::BuildManagerGrpcClient;
use avenra_runtime_node_mgr_client::NodeManagerGrpcClient;
use avenra_runtime_sbx_mgr_client::SandboxManagerGrpcClient;
use avenra_runtime_template_mgr_client::TemplateManagerGrpcClient;

/// Holds tonic client connections to all internal backend services.
///
/// Tonic clients are cheaply cloneable -- they share the underlying HTTP/2
/// channel, so cloning gives a new handle over the same connection.
///
/// template-manager is deployed on both worker and builder nodes.
/// (template-builder is builder-only, but that is a separate service.)
#[derive(Clone)]
pub struct BackendClients {
    sbx: SandboxManagerGrpcClient,
    node: NodeManagerGrpcClient,
    tmpl: TemplateManagerGrpcClient,
    build_mgr: BuildManagerGrpcClient,
}

#[derive(Debug, thiserror::Error)]
pub enum BackendClientError {
    #[error("connect to sandbox-manager failed: {0}")]
    Sandbox(#[from] avenra_runtime_sbx_mgr_client::ConnectError),
    #[error("connect to node-manager failed: {0}")]
    Node(#[from] avenra_runtime_node_mgr_client::ConnectError),
    #[error("connect to template-manager failed: {0}")]
    Template(#[from] avenra_runtime_template_mgr_client::ConnectError),
}

impl BackendClients {
    /// Build backend clients without opening dependency connections immediately.
    ///
    /// node-api must be able to serve NodeInfo and gRPC health while a dependency is
    /// down or returning bad data. Individual RPCs surface backend failures.
    pub fn lazy() -> Self {
        Self {
            sbx: avenra_runtime_sbx_mgr_client::lazy(),
            node: avenra_runtime_node_mgr_client::lazy(),
            tmpl: avenra_runtime_template_mgr_client::lazy(),
            build_mgr: build_mgr_lazy(),
        }
    }

    /// Connect to node-local internal services. Build-manager uses a lazy TCP
    /// client so node-api can start before a rolling Deployment settles.
    pub async fn connect() -> Result<Self, BackendClientError> {
        let sbx = avenra_runtime_sbx_mgr_client::connect().await?;
        let node = avenra_runtime_node_mgr_client::connect().await?;
        let tmpl = avenra_runtime_template_mgr_client::connect().await?;
        Ok(Self {
            sbx,
            node,
            tmpl,
            build_mgr: build_mgr_lazy(),
        })
    }

    pub fn sbx(&self) -> SandboxManagerGrpcClient {
        self.sbx.clone()
    }

    pub fn node(&self) -> NodeManagerGrpcClient {
        self.node.clone()
    }

    pub fn tmpl(&self) -> TemplateManagerGrpcClient {
        self.tmpl.clone()
    }

    pub fn build_mgr(&self) -> BuildManagerGrpcClient {
        self.build_mgr.clone()
    }
}

fn build_mgr_lazy() -> BuildManagerGrpcClient {
    avenra_runtime_build_mgr_client::connect_lazy()
        .expect("BuildManager endpoint, TLS trust and bearer credentials must be valid")
}

#[cfg(all(test, feature = "k8s"))]
mod policy_auth_tests;

#[cfg(all(test, feature = "k8s"))]
mod template_auth_tests;

#[cfg(all(test, feature = "k8s"))]
mod template_transfer_auth_tests;

#[cfg(test)]
mod stream_tests;

#[cfg(test)]
mod build_tests;
