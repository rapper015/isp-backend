"""Add the complete NAS/MikroTik orchestration schema.

This additive migration extends the NAS draft foundation with the full managed
field set, capability/health/remote-object/secret-rotation persistence, and
assignment-level RADIUS port overrides.
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_nas_full_orchestration"
down_revision = "0004_nas_desired_configuration"
branch_labels = None
depends_on = None

_NAS_NEW_COLUMNS = (
    sa.Column("description", sa.String(500), nullable=True),
    sa.Column("site", sa.String(128), nullable=True),
    sa.Column("management_host", sa.String(255), nullable=True),
    sa.Column("management_port", sa.Integer(), nullable=False, server_default="8729"),
    sa.Column("management_protocol", sa.String(16), nullable=False, server_default="api_ssl"),
    sa.Column("api_mode", sa.String(16), nullable=False, server_default="auto"),
    sa.Column("tls_verify", sa.Boolean(), nullable=False, server_default=sa.true()),
    sa.Column("radius_source_ipv6", sa.String(45), nullable=True),
    sa.Column("model", sa.String(64), nullable=True),
    sa.Column("serial_number", sa.String(64), nullable=True),
    sa.Column("routeros_version", sa.String(32), nullable=True),
    sa.Column("architecture", sa.String(32), nullable=True),
    sa.Column("board_name", sa.String(64), nullable=True),
    sa.Column("identity", sa.String(64), nullable=True),
    sa.Column("time_zone", sa.String(64), nullable=True),
    sa.Column("lifecycle_status", sa.String(32), nullable=False, server_default="DRAFT"),
    sa.Column("connection_status", sa.String(24), nullable=False, server_default="UNKNOWN"),
    sa.Column("configuration_status", sa.String(24), nullable=False, server_default="NONE"),
    sa.Column("registration_status", sa.String(32), nullable=False, server_default="NOT_REQUIRED"),
    sa.Column("last_connected_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_discovery_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_configuration_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("failure_reason", sa.String(500), nullable=True),
)

_CREDENTIAL_NEW_COLUMNS = (
    sa.Column("api_port", sa.Integer(), nullable=False, server_default="8729"),
    sa.Column("tls_settings", sa.JSON(), nullable=False),
    sa.Column("certificate_reference", sa.String(255), nullable=True),
    sa.Column("last_rotated_at", sa.DateTime(timezone=True), nullable=True),
)

_ASSIGNMENT_NEW_COLUMNS = (
    sa.Column("auth_port", sa.Integer(), nullable=True),
    sa.Column("accounting_port", sa.Integer(), nullable=True),
    sa.Column("coa_port", sa.Integer(), nullable=True),
    sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="3000"),
    sa.Column("last_synchronized_at", sa.DateTime(timezone=True), nullable=True),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_nas = {column["name"] for column in inspector.get_columns("aaa_nas")}
    for column in _NAS_NEW_COLUMNS:
        if column.name not in existing_nas:
            op.add_column("aaa_nas", column)
    existing_credentials = {column["name"] for column in inspector.get_columns("nas_credentials")}
    for column in _CREDENTIAL_NEW_COLUMNS:
        if column.name not in existing_credentials:
            op.add_column("nas_credentials", column)
    existing_assignments = {column["name"] for column in inspector.get_columns("nas_radius_assignments")}
    for column in _ASSIGNMENT_NEW_COLUMNS:
        if column.name not in existing_assignments:
            op.add_column("nas_radius_assignments", column)
    op.create_table(
        "nas_capabilities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("nas_id", sa.Uuid(), sa.ForeignKey("aaa_nas.id"), index=True, nullable=False),
        sa.Column("version", sa.String(32), nullable=False, server_default="0"),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("flags", sa.JSON(), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("nas_id", "version", name="uq_nas_capability_version"),
    )
    op.create_table(
        "nas_health_checks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("nas_id", sa.Uuid(), sa.ForeignKey("aaa_nas.id"), index=True, nullable=False),
        sa.Column("check_type", sa.String(48), index=True, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("diagnostic", sa.JSON(), nullable=False),
        sa.Column("failure_reason", sa.String(500), nullable=True),
    )
    op.create_table(
        "nas_remote_objects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("nas_id", sa.Uuid(), sa.ForeignKey("aaa_nas.id"), index=True, nullable=False),
        sa.Column("object_type", sa.String(48), nullable=False),
        sa.Column("remote_object_id", sa.String(128), nullable=False),
        sa.Column("backend_assignment_id", sa.Uuid(), index=True, nullable=True),
        sa.Column("last_observed_attributes", sa.JSON(), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ownership", sa.String(24), nullable=False, server_default="UNKNOWN"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("nas_id", "object_type", "remote_object_id", name="uq_nas_remote_object"),
    )
    op.create_table(
        "nas_operation_locks",
        sa.Column("nas_id", sa.Uuid(), primary_key=True),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    reveal_table = "nas_secret_reveals"
    reveal_columns = {column["name"] for column in sa.inspect(bind).get_columns(reveal_table)}
    if "rotation_id" not in reveal_columns:
        op.add_column(reveal_table, sa.Column("rotation_id", sa.Uuid(), sa.ForeignKey("nas_secret_rotations.id"), nullable=True))
    if "secret_ciphertext" not in reveal_columns:
        op.add_column(reveal_table, sa.Column("secret_ciphertext", sa.Text(), nullable=True))
    op.create_table(
        "nas_secret_rotations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("nas_id", sa.Uuid(), sa.ForeignKey("aaa_nas.id"), index=True, nullable=False),
        sa.Column("assignment_id", sa.Uuid(), sa.ForeignKey("nas_radius_assignments.id"), index=True, nullable=False),
        sa.Column("state", sa.String(40), nullable=False, server_default="ROTATION_DRAFT"),
        sa.Column("old_secret_ciphertext", sa.Text(), nullable=True),
        sa.Column("old_secret_version", sa.Integer(), nullable=True),
        sa.Column("new_secret_ciphertext", sa.Text(), nullable=False),
        sa.Column("new_secret_version", sa.Integer(), nullable=False),
        sa.Column("freeradius_confirmations", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False, server_default="internal-radius"),
        sa.Column("rollback_available_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("nas_secret_reveals", "secret_ciphertext")
    op.drop_column("nas_secret_reveals", "rotation_id")
    for name in ("nas_secret_rotations", "nas_operation_locks", "nas_remote_objects", "nas_health_checks", "nas_capabilities"):
        op.drop_table(name)
    for column in reversed(_ASSIGNMENT_NEW_COLUMNS):
        op.drop_column("nas_radius_assignments", column.name)
    for column in reversed(_CREDENTIAL_NEW_COLUMNS):
        op.drop_column("nas_credentials", column.name)
    for column in reversed(_NAS_NEW_COLUMNS):
        op.drop_column("aaa_nas", column.name)
