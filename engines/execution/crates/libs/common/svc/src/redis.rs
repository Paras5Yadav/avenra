// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
//! Reusable Redis client construction for Avenra services.

use ::redis::{RedisResult, cluster::ClusterClient};

/// Builds a lazy cluster-aware client from a Redis URL.
///
/// Construction validates configuration without connecting. The Redis crate
/// performs topology discovery and routing when a consumer requests a
/// connection.
pub fn cluster_client_from_url(url: &str) -> RedisResult<ClusterClient> {
    ClusterClient::new([url])
}

#[cfg(test)]
mod tests;
