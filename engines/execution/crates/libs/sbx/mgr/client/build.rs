// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
fn main() -> anyhow::Result<()> {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR")?;
    let proto_root = std::path::Path::new(&manifest_dir).join("../../../../../proto");
    let out_dir = std::env::var("OUT_DIR")?;
    let descriptor_path = std::path::Path::new(&out_dir).join("sandbox_manager_descriptor.bin");

    let mgr_dir = proto_root.join("sandbox/manager/v1");
    let mgr_file = mgr_dir.join("manager.proto");
    let proto_dirs = [
        proto_root,
        mgr_dir,
        avenra_runtime_avenrad_build::protobuf_include_dir()?,
    ];
    let builder = tonic_prost_build::configure()
        .extern_path(
            ".avenra.avenrad.operations.v1",
            "::avenra_runtime_avenrad_common::proto::operations",
        )
        .extern_path(
            ".avenra.execution.v1",
            "::avenra_runtime_execution_common::proto",
        )
        .extern_path(".avenra.net.policy.v1", "::avenra_runtime_net_common::proto")
        .extern_path(
            ".avenra.net.gateway.v1",
            "::avenra_runtime_net_gateway_client::proto",
        );
    avenra_runtime_common_proto_build::compile_protos_with_descriptor_set(
        builder,
        &descriptor_path,
        &[mgr_file],
        &proto_dirs,
    )?;

    Ok(())
}
