// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
fn main() -> anyhow::Result<()> {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR")?;
    let root = std::path::Path::new(&manifest_dir).join("../../../../../proto");
    let common = root.join("node/api/v1/common.proto");
    avenra_runtime_common_proto_build::compile_protos(
        tonic_prost_build::configure().build_server(false),
        &[common],
        &[root],
    )?;
    Ok(())
}
