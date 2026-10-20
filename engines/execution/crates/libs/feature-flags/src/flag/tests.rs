// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
use super::*;
use crate::{
    AVENRA_API_CLIENT_CONCURRENCY_LIMITER_FLAG, AVENRA_CLUSTER_API_CLIENT_SIDE_BALANCING_FLAG,
    AVENRA_CLUSTER_RATE_LIMITER_FLAG, AVENRA_NATIVE_CREATE_POOL_CLAIM_FLAG,
};

/// A declaration reports the key it was given and the fallback chosen for it,
/// and renders as the key so a log field or a metric tag reads the same as the
/// flag list does.
#[test]
fn a_declaration_carries_its_key_and_its_fallback() {
    const GATE: BoolFlag = BoolFlag::new("avenra-test-gate", true);
    const COUNT: IntegerFlag = IntegerFlag::new("avenra-test-count", 9);
    const RATE: FloatFlag = FloatFlag::new("avenra-test-rate", 0.25);
    const MODE: StringFlag = StringFlag::new("avenra-test-mode", "off");
    const SHAPE: JsonFlag = JsonFlag::new("avenra-test-shape");

    assert_eq!(
        (GATE.key(), GATE.when_unresolved()),
        ("avenra-test-gate", true)
    );
    assert_eq!(
        (COUNT.key(), COUNT.when_unresolved()),
        ("avenra-test-count", 9)
    );
    assert_eq!(RATE.key(), "avenra-test-rate");
    // Bit-compared: `clippy::float_cmp` is on repo-wide and the property is
    // that the declared value came back unchanged.
    assert_eq!(RATE.when_unresolved().to_bits(), 0.25_f64.to_bits());
    assert_eq!(
        (MODE.key(), MODE.when_unresolved()),
        ("avenra-test-mode", "off")
    );
    assert_eq!(SHAPE.key(), "avenra-test-shape");

    assert_eq!(GATE.to_string(), "avenra-test-gate");
    assert_eq!(MODE.to_string(), "avenra-test-mode");
    assert_eq!(SHAPE.to_string(), "avenra-test-shape");
}

/// The fallbacks the shipped boolean flags declare, pinned as values rather
/// than described in prose.
///
/// Each one is load-bearing in a direction the other flags do not share: the
/// two that fall back *on* are guardrails whose flag exists only to switch
/// them off, so an unreachable provider that flipped them to `false` would
/// silently remove a limiter that the deployment asked for.
/// The one that falls back *off* is a rollout, where the same unreachable
/// provider must not enable anything nobody turned on. Flipping any of these
/// is a deliberate product decision; this test is what makes it impossible to
/// do by accident while editing a nearby line.
#[test]
fn the_shipped_boolean_flags_declare_the_fallbacks_their_features_need() {
    for (flag, expected) in [
        (AVENRA_CLUSTER_RATE_LIMITER_FLAG, true),
        (AVENRA_API_CLIENT_CONCURRENCY_LIMITER_FLAG, true),
        (AVENRA_CLUSTER_API_CLIENT_SIDE_BALANCING_FLAG, false),
        (AVENRA_NATIVE_CREATE_POOL_CLAIM_FLAG, false),
    ] {
        assert_eq!(
            flag.when_unresolved(),
            expected,
            "{flag} declares the wrong fallback",
        );
    }
}
