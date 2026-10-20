// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
use clap::{ArgAction, Parser};
use avenra_runtime_avenrad_guest_server::SpacedGuestRunConfig;

#[derive(Parser)]
#[command(
    name = "avenrad-svc",
    about = "Avenra guest daemon",
    version,
    disable_version_flag = true
)]
struct Cli {
    /// Print version.
    #[arg(
        short = 'v',
        long = "version",
        action = ArgAction::Version,
        required = false
    )]
    _version: (),

    /// Hold the calling unit's cgroup open and do nothing else, for
    /// `avenra-workloads.service`: systemd removes an empty cgroup, and this
    /// binary is in every guest, where `sleep` may not be.
    #[arg(long = "hold-cgroup", hide = true)]
    hold_cgroup: bool,
}

impl From<Cli> for SpacedGuestRunConfig {
    fn from(_cli: Cli) -> Self {
        // The guest daemon takes no flags: the microVM's environment is its
        // configuration, and the library reads it.
        Self::default()
    }
}

fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();
    if cli.hold_cgroup {
        // No runtime, telemetry or cgroup setup: SIGTERM from the unit's stop
        // ends the process with the default disposition.
        loop {
            std::thread::park();
        }
    }
    Ok(avenra_runtime_avenrad_guest_server::run(cli.into())?)
}
