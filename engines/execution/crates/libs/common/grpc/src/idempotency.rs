// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
use avenra_runtime_common_core::types::IdempotencyKey;

pub fn generated_idempotency_key() -> IdempotencyKey {
    IdempotencyKey::parse(uuid::Uuid::now_v7().to_string())
        .expect("UUIDv7 string is a valid idempotency key")
}
