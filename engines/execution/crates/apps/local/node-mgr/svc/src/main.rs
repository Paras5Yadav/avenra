// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
//! `avenra-local-nodemgr-svc` — the local non-Firecracker node-manager.
//!
//! Binds the production node-manager Unix socket and serves
//! `space.node.v1.NodeManager` so an unmodified sandbox-manager / node-api can
//! drive real sandboxes on a kind cluster without KVM or btrfs.

use std::path::PathBuf;

use clap::Parser;
use avenra_runtime_local_node_mgr::{Config, LocalNodeManager};
use avenra_runtime_node_mgr_client::proto::node_manager_server::NodeManagerServer;
use tokio_stream::wrappers::UnixListenerStream;
use tokio_util::sync::CancellationToken;

#[derive(Parser)]
#[command(
    name = "avenra-local-nodemgr-svc",
    about = "Local non-Firecracker node-manager for the Avenra kind smoke harness",
    version,
    disable_version_flag = true
)]
struct Cli {
    /// Print version.
    #[arg(short = 'v', long = "version", action = clap::ArgAction::Version)]
    _version: (),

    /// node-manager Unix socket to bind (defaults to the production path).
    #[arg(long)]
    socket: Option<PathBuf>,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let telemetry = avenra_runtime_common_svc::init_service("avenra-local-node-manager")?;
    let cli = Cli::parse();
    let config = Config::resolve(cli.socket);

    let listener = avenra_runtime_common_svc::bind_unix_socket(
        &config.socket,
        // World-connectable: every node service runs as root in this harness, so
        // the socket-group gate the production node-manager enforces is moot.
        avenra_runtime_common_svc::UnixSocketOptions::new(0o777),
    )?;
    tracing::info!(
        socket = %config.socket.display(),
        seed_rootfs = %config.seed_rootfs.display(),
        "avenra-local-node-manager listening",
    );

    let service = LocalNodeManager::new(config);

    let shutdown = CancellationToken::new();
    {
        let shutdown = shutdown.clone();
        tokio::spawn(async move {
            if let Ok(mut sig) =
                tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            {
                sig.recv().await;
                tracing::info!("received SIGTERM, shutting down");
                shutdown.cancel();
            }
        });
    }

    let (_, health_service) = avenra_runtime_common_svc::grpc_health_service();
    avenra_runtime_common_svc::grpc_server_builder()
        .layer(avenra_runtime_common_svc::grpc_trace_layer())
        .add_service(health_service)
        .add_service(NodeManagerServer::new(service))
        .serve_with_incoming_shutdown(UnixListenerStream::new(listener), async move {
            shutdown.cancelled().await
        })
        .await?;

    avenra_runtime_common_svc::shutdown_telemetry(telemetry).await;
    Ok(())
}
