// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
fn main() -> anyhow::Result<()> {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR")?;
    let proto_root = std::path::Path::new(&manifest_dir).join("../../../../../proto");

    let gtw_file = proto_root.join("net/gateway/v1/gateway.proto");
    avenra_runtime_common_proto_build::compile_protos(
        tonic_prost_build::configure()
            .extern_path(".avenra.net.policy.v1", "::avenra_runtime_net_common::proto"),
        &[gtw_file],
        &[proto_root],
    )?;

    Ok(())
}
