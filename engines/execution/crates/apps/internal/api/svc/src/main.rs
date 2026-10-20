// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
use anyhow::Context as _;
use clap::Parser;
use avenra_runtime_internal_api_server::{DEFAULT_LISTEN_ADDR, InternalApiRunConfig, SERVICE_NAME};

// Same allocator choice as avenra-api-svc: glibc arenas retain the peak
// of each complete-inventory publish, so RSS tracks fleet inventory size
// rather than live state. Static musl builds keep the target allocator.
#[cfg(all(target_os = "linux", target_env = "gnu"))]
#[global_allocator]
static GLOBAL: tikv_jemallocator::Jemalloc = tikv_jemallocator::Jemalloc;

#[derive(Parser)]
#[command(
    name = "avenra-internal-api-svc",
    about = "Avenra cluster control-plane gRPC API (ClusterApi over the Postgres SSOT)",
    version,
    disable_version_flag = true
)]
struct Cli {
    /// Print version.
    #[arg(short = 'v', long = "version", action = clap::ArgAction::Version)]
    _version: (),

    /// TCP address to listen on
    #[arg(long, default_value = DEFAULT_LISTEN_ADDR)]
    listen: String,
}

impl From<Cli> for InternalApiRunConfig {
    fn from(cli: Cli) -> Self {
        Self { listen: cli.listen }
    }
}

fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();
    avenra_runtime_common_svc::tokio_runtime::build_multi_thread_runtime()
        .context("failed to configure cluster-api Tokio runtime")?
        .block_on(async {
            // `init_service` installs the process-global aws-lc-rs rustls crypto
            // provider used by the kube TokenReview client; no separate install here.
            let telemetry = avenra_runtime_common_svc::init_service(SERVICE_NAME)?;
            let shutdown_token = avenra_runtime_common_svc::shutdown_token();
            let result = avenra_runtime_internal_api_server::run(cli.into(), shutdown_token).await;
            avenra_runtime_common_svc::shutdown_telemetry(telemetry).await;
            Ok(result?)
        })
}
