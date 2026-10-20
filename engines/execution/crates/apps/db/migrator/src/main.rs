// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
use clap::Parser;
use avenra_runtime_db_migrate::{Database, DbMigrateRunConfig, SERVICE_NAME};

#[derive(Parser)]
#[command(
    name = "avenra-db-migrator",
    about = "Avenra SQLx migration runner",
    version,
    disable_version_flag = true
)]
struct Cli {
    /// Print version.
    #[arg(short = 'v', long = "version", action = clap::ArgAction::Version)]
    _version: (),

    #[command(subcommand)]
    database: Database,
}

impl From<Cli> for DbMigrateRunConfig {
    fn from(cli: Cli) -> Self {
        Self {
            database: cli.database,
        }
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    avenra_runtime_common_cli::init_logging_with_startup_banner(SERVICE_NAME);
    let cli = Cli::parse();

    Ok(avenra_runtime_db_migrate::run(cli.into()).await?)
}
