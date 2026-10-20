// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
fn main() -> anyhow::Result<()> {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR")?;
    let proto_root = std::path::Path::new(&manifest_dir).join("../../../../../proto");
    let runtime_file = proto_root.join("node/runtime/v1/runtime.proto");
    avenra_runtime_common_proto_build::compile_protos(
        tonic_prost_build::configure(),
        &[runtime_file],
        &[proto_root],
    )?;
    Ok(())
}
