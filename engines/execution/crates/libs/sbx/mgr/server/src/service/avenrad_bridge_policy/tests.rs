// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
use super::*;
use prost::Message;
use std::collections::BTreeSet;

fn served_avenrad_rpcs() -> BTreeSet<String> {
    let descriptor = prost_types::FileDescriptorSet::decode(
        avenra_runtime_sbx_mgr_client::proto::FILE_DESCRIPTOR_SET,
    )
    .expect("sandbox-manager descriptor decodes");

    descriptor
        .file
        .into_iter()
        .flat_map(|file| file.service)
        .filter(|service| service.name.as_deref() == Some("SandboxManager"))
        .flat_map(|service| service.method)
        .filter_map(|method| method.name)
        .filter(|name| name.starts_with("Spaced"))
        .collect()
}

#[test]
fn every_served_avenrad_rpc_has_exactly_one_policy() {
    let served = served_avenrad_rpcs();
    let classified = SpacedRpc::ALL
        .iter()
        .map(|rpc| rpc.as_str().to_string())
        .collect::<BTreeSet<_>>();

    assert_eq!(classified, served);
    assert_eq!(
        SpacedRpc::ALL.len(),
        classified.len(),
        "duplicate bridge policy entry"
    );
}
