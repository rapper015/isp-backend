"""Cross-service messaging: outbox flush/publish, idempotent inbound consumers.

Consumers never trust event payloads for tenant/customer identity — they are
validated against the authenticated event metadata. Delivery is at-least-once;
deduplication is guaranteed by the inbox."""
