// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
use super::{NodePathError, NodeSpacePath};

#[test]
fn node_avenra_path_rejects_relative_and_parent_traversal() {
    assert_eq!(
        NodeSpacePath::parse("avenra").unwrap_err(),
        NodePathError::NotAbsolute
    );
    assert_eq!(
        NodeSpacePath::parse("/avenra/../etc").unwrap_err(),
        NodePathError::ParentTraversal
    );
    NodeSpacePath::parse("/space").unwrap();
}
