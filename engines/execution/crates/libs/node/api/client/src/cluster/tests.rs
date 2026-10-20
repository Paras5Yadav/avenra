// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
use super::*;

#[test]
fn endpoint_discovery_accepts_only_off_and_kubernetes() {
    for value in ["off", " off ", "OFF"] {
        assert_eq!(value.parse(), Ok(EndpointDiscovery::Off));
    }
    for value in ["kubernetes", " Kubernetes "] {
        assert_eq!(value.parse(), Ok(EndpointDiscovery::Kubernetes));
    }
    assert_eq!(EndpointDiscovery::Off.as_str(), "off");
    assert_eq!(EndpointDiscovery::Kubernetes.to_string(), "kubernetes");
    assert_eq!(
        "dns".parse::<EndpointDiscovery>(),
        Err(ParseEndpointDiscoveryError("dns".to_owned()))
    );
}

#[cfg(feature = "kubernetes")]
#[test]
fn discovery_service_comes_from_the_cluster_api_endpoint() {
    let uri: Uri = "http://avenra-internal-api.avenra.svc:9800".parse().unwrap();
    assert_eq!(kubernetes_service_name(&uri), Some("avenra-internal-api"));
}

#[test]
fn unconfigured_token_errors_classify_as_missing() {
    for error in [
        BearerTokenError::Missing {
            path_env: "AVENRA_CLUSTER_API_TOKEN_PATH",
            default_path: "/var/run/avenra-auth/cluster-api.token".into(),
            fallback_env: "AVENRA_INTERNAL_TOKEN",
        },
        BearerTokenError::EmptyEnv {
            env_var: "AVENRA_INTERNAL_TOKEN",
        },
    ] {
        assert!(matches!(
            classify_token_error(error),
            ConnectClusterApiError::MissingToken(_)
        ));
    }
}

#[test]
fn broken_token_source_errors_classify_as_token_source() {
    for error in [
        BearerTokenError::MissingFile {
            path: "/var/run/avenra-auth/cluster-api.token".into(),
        },
        BearerTokenError::EmptyFile {
            path: "/var/run/avenra-auth/cluster-api.token".into(),
        },
        BearerTokenError::ReadFile {
            path: "/var/run/avenra-auth/cluster-api.token".into(),
            source: std::io::Error::from(std::io::ErrorKind::PermissionDenied),
        },
    ] {
        assert!(matches!(
            classify_token_error(error),
            ConnectClusterApiError::TokenSource(_)
        ));
    }
}
