// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
fn main() -> anyhow::Result<()> {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR")?;
    let proto_root = std::path::Path::new(&manifest_dir).join("../../../../../proto");

    let cred_file = proto_root.join("credential/manager/v1/credential_manager.proto");
    let builder = tonic_prost_build::configure()
        .type_attribute(
            ".avenra.credential.manager.v1.ExchangeTokenRequest",
            "#[derive(zeroize::Zeroize, zeroize::ZeroizeOnDrop)]",
        )
        .type_attribute(
            ".avenra.credential.manager.v1.ExchangeTokenResponse",
            "#[derive(zeroize::Zeroize, zeroize::ZeroizeOnDrop)]",
        )
        .type_attribute(
            ".avenra.credential.manager.v1.AwsCredentials",
            "#[derive(zeroize::Zeroize, zeroize::ZeroizeOnDrop)]",
        )
        .type_attribute(
            ".avenra.credential.manager.v1.IssueDekResponse",
            "#[derive(zeroize::Zeroize, zeroize::ZeroizeOnDrop)]",
        )
        .type_attribute(
            ".avenra.credential.manager.v1.UnwrapDekResponse",
            "#[derive(zeroize::Zeroize, zeroize::ZeroizeOnDrop)]",
        )
        .type_attribute(
            ".avenra.credential.manager.v1.EncryptRequest",
            "#[derive(zeroize::Zeroize, zeroize::ZeroizeOnDrop)]",
        )
        .type_attribute(
            ".avenra.credential.manager.v1.DecryptResponse",
            "#[derive(zeroize::Zeroize, zeroize::ZeroizeOnDrop)]",
        )
        .type_attribute(
            ".avenra.credential.manager.v1.GetOrIssueSandboxVolumeDekResponse",
            "#[derive(zeroize::Zeroize, zeroize::ZeroizeOnDrop)]",
        )
        .type_attribute(
            ".avenra.credential.manager.v1.ResolveNetworkCredentialsResponse",
            "#[derive(zeroize::Zeroize, zeroize::ZeroizeOnDrop)]",
        )
        .skip_debug([
            ".avenra.credential.manager.v1.ExchangeTokenRequest",
            ".avenra.credential.manager.v1.ExchangeTokenResponse",
            ".avenra.credential.manager.v1.AwsCredentials",
            ".avenra.credential.manager.v1.IssueDekResponse",
            ".avenra.credential.manager.v1.UnwrapDekResponse",
            ".avenra.credential.manager.v1.EncryptRequest",
            ".avenra.credential.manager.v1.DecryptResponse",
            ".avenra.credential.manager.v1.GetOrIssueSandboxVolumeDekResponse",
            ".avenra.credential.manager.v1.ResolveNetworkCredentialsResponse",
        ])
        .extern_path(
            ".avenra.credential.manager.v1.ResolvedNetworkCredential",
            "::avenra_runtime_credential_injection_core::proto::ResolvedNetworkCredential",
        );
    avenra_runtime_common_proto_build::compile_protos(builder, &[cred_file], &[proto_root])?;

    Ok(())
}
