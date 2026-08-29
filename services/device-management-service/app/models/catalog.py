"""Device model catalogue: manufacturers, models, variants, data models,
capabilities, parameter definitions/mappings, vendor quirks and supported
RPCs/diagnostics. Vendor-specific parameter paths are isolated here in
versioned mappings — never hardcoded in views."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, UuidPk


class DeviceManufacturer(Base, Timestamped, UuidPk):
    __tablename__ = "device_manufacturers"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    oui: Mapped[str | None] = mapped_column(String(8), index=True, nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class DeviceModel(Base, Timestamped, UuidPk):
    __tablename__ = "device_models"
    __table_args__ = (UniqueConstraint("manufacturer_id", "model_name", name="uq_device_model_name"),)

    manufacturer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_manufacturers.id"), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    oui: Mapped[str | None] = mapped_column(String(8), index=True, nullable=True)
    product_class: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    device_kind: Mapped[str] = mapped_column(String(32), default="ONT", nullable=False)  # ONT / ROUTER / ONU / ...
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class DeviceModelVariant(Base, Timestamped, UuidPk):
    __tablename__ = "device_model_variants"
    __table_args__ = (UniqueConstraint("model_id", "hardware_version", name="uq_device_model_hw"),)

    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_models.id"), nullable=False)
    hardware_version: Mapped[str] = mapped_column(String(64), nullable=False)
    data_model_family: Mapped[str] = mapped_column(String(24), default="TR181", nullable=False)  # TR098 / TR181
    data_model_version: Mapped[str] = mapped_column(String(16), default="2.0", nullable=False)
    reboot_supported: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    factory_reset_supported: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    firmware_download_supported: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    rollback_capability: Mapped[str] = mapped_column(String(32), default="NONE", nullable=False)
    known_limitations: Mapped[str | None] = mapped_column(Text, nullable=True)


class DeviceDataModel(Base, Timestamped, UuidPk):
    """A named, versioned data model (e.g. TR-098 R1.0, TR-181 R2.1)."""

    __tablename__ = "device_data_models"
    __table_args__ = (UniqueConstraint("family", "version", name="uq_device_data_model"),)

    family: Mapped[str] = mapped_column(String(24), nullable=False)
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    root_path: Mapped[str] = mapped_column(String(64), default="Device.", nullable=False)


class DeviceCapability(Base, Timestamped, UuidPk):
    __tablename__ = "device_capabilities"
    __table_args__ = (UniqueConstraint("model_variant_id", "name", name="uq_device_capability"),)

    model_variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_model_variants.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. WIFI_5GHZ, OPTICAL_RX_TX, DIAGNOSTIC_PING
    state: Mapped[str] = mapped_column(String(32), default="UNVERIFIED", nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class ParameterDefinition(Base, Timestamped, UuidPk):
    """Vendor-neutral logical parameter (e.g. WIFI_SSID_24GHZ)."""

    __tablename__ = "device_parameter_definitions"
    __table_args__ = (UniqueConstraint("code", name="uq_device_parameter_code"),)

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    data_type: Mapped[str] = mapped_column(String(24), default="STRING", nullable=False)
    writable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sensitive_category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class ParameterMapping(Base, Timestamped, UuidPk):
    """Maps a vendor-neutral parameter to vendor/model-specific paths."""

    __tablename__ = "device_parameter_mappings"
    __table_args__ = (UniqueConstraint("definition_id", "model_variant_id", name="uq_device_parameter_mapping"),)

    definition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_parameter_definitions.id"), nullable=False)
    model_variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_model_variants.id"), index=True, nullable=False)
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    read_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    data_model_family: Mapped[str] = mapped_column(String(24), default="TR181", nullable=False)
    writable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mapping_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    value_transform: Mapped[str | None] = mapped_column(String(64), nullable=True)  # optional normalize fn key


class VendorQuirk(Base, Timestamped, UuidPk):
    __tablename__ = "device_vendor_quirks"

    model_variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_model_variants.id"), index=True, nullable=False)
    quirk: Mapped[str] = mapped_column(String(255), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class SupportedAction(Base, Timestamped, UuidPk):
    __tablename__ = "device_supported_actions"
    __table_args__ = (UniqueConstraint("model_variant_id", "action", name="uq_device_supported_action"),)

    model_variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_model_variants.id"), index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)  # REBOOT / FACTORY_RESET / ...
    rpc_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requires_elevated_permission: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SupportedDiagnostic(Base, Timestamped, UuidPk):
    __tablename__ = "device_supported_diagnostics"
    __table_args__ = (UniqueConstraint("model_variant_id", "diagnostic", name="uq_device_supported_diag"),)

    model_variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_model_variants.id"), index=True, nullable=False)
    diagnostic: Mapped[str] = mapped_column(String(40), nullable=False)  # PING / TRACEROUTE / ...
    rpc_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parameter_evidence: Mapped[str | None] = mapped_column(String(255), nullable=True)
