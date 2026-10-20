// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
//! Directional host support for guest releases, independent of host identity.

use std::sync::LazyLock;

/// Keep the legacy floor until live guests, templates, and dormant snapshots
/// have a documented migration path. Do not derive it from CARGO_PKG_VERSION.
pub const SUPPORTED_AvenraD_GUEST_VERSIONS: &str = ">=0.1.0, <0.9.0";

static SUPPORTED_GUESTS: LazyLock<semver::VersionReq> = LazyLock::new(|| {
    semver::VersionReq::parse(SUPPORTED_AvenraD_GUEST_VERSIONS)
        .expect("supported avenrad guest requirement is valid SemVer")
});

#[derive(Debug, thiserror::Error)]
pub enum GuestCompatibilityError {
    #[error("missing avenrad guest version or compatibility family")]
    MissingIdentity,
    #[error("invalid avenrad guest version '{version}': {source}")]
    InvalidVersion {
        version: String,
        #[source]
        source: semver::Error,
    },
    #[error(
        "avenrad guest version {version} is unsupported; supported versions: {SUPPORTED_AvenraD_GUEST_VERSIONS} (stable releases only)"
    )]
    UnsupportedVersion { version: semver::Version },
    #[error(
        "avenrad guest version {version} requires compatibility family {expected}, found '{found}'"
    )]
    InconsistentFamily {
        version: semver::Version,
        expected: String,
        found: String,
    },
}

/// Validate artifact metadata or a live guest's readiness identity. The family
/// describes the guest; it is not a requirement that host and guest match.
pub fn validate_avenrad_guest_compatibility(
    version: Option<&str>,
    family: Option<&str>,
) -> Result<(), GuestCompatibilityError> {
    let (Some(version), Some(family)) = (version, family) else {
        return Err(GuestCompatibilityError::MissingIdentity);
    };
    let version = semver::Version::parse(version).map_err(|source| {
        GuestCompatibilityError::InvalidVersion {
            version: version.to_owned(),
            source,
        }
    })?;
    if !SUPPORTED_GUESTS.matches(&version) {
        return Err(GuestCompatibilityError::UnsupportedVersion { version });
    }
    let expected = compatibility_family(&version);
    if family != expected {
        return Err(GuestCompatibilityError::InconsistentFamily {
            version,
            expected,
            found: family.to_owned(),
        });
    }
    Ok(())
}

pub(super) fn compatibility_family(version: &semver::Version) -> String {
    if version.major == 0 {
        if version.minor == 0 {
            format!("0.0.{}", version.patch)
        } else {
            format!("0.{}", version.minor)
        }
    } else {
        version.major.to_string()
    }
}

#[cfg(test)]
mod tests;
