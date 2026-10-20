// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
fn main() -> anyhow::Result<()> {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR")?;
    let proto_root = std::path::Path::new(&manifest_dir).join("../../../../proto");
    let proto_file = proto_root.join("template/manager/v1/manager.proto");
    let builder = tonic_prost_build::configure()
        .extern_path(
            ".avenra.sandbox.manager.v1",
            "::avenra_runtime_sbx_mgr_client::proto",
        )
        .extern_path(".avenra.net.policy.v1", "::avenra_runtime_net_common::proto")
        .extern_path(
            ".avenra.execution.v1",
            "::avenra_runtime_execution_common::proto",
        );
    avenra_runtime_common_proto_build::compile_protos(builder, &[proto_file], &[proto_root])?;
    Ok(())
}
