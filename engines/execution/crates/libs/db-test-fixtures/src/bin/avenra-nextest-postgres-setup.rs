// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
use std::env;
use std::error::Error;

use avenra_runtime_db_test_fixtures::initialize_postgres_template;

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error + Send + Sync>> {
    let admin_database_url = env::args()
        .nth(1)
        .ok_or("usage: avenra-nextest-postgres-setup <postgres-admin-database-url>")?;
    initialize_postgres_template(&admin_database_url).await
}
