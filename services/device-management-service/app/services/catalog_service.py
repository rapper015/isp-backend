"""Device model catalogue service: manufacturers, models, variants, data
models, parameter definitions, versioned mappings, capabilities and
capability discovery. Vendor-specific paths live only here (versioned), never
in views or task bodies."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import NotFoundError, ValidationError
from ..models import (
    DeviceCapability,
    DeviceDataModel,
    DeviceManufacturer,
    DeviceModel,
    DeviceModelVariant,
    ParameterDefinition,
    ParameterMapping,
    SupportedAction,
    SupportedDiagnostic,
)
from .audit_service import audit, correlation

_DEFAULT_MANUFACTURERS = [
    {"name": "FiberHome", "oui": "A4B1C1"},
    {"name": "Huawei", "oui": "FCFC48"},
    {"name": "ZTE", "oui": "001E73"},
]

_DEFAULT_MODELS = [
    {"manufacturer": "FiberHome", "model_name": "AN5506-04-F1", "product_class": "AN5506",
     "device_kind": "ONT", "oui": "A4B1C1", "hardware_versions": ["V1.0"], "family": "TR181", "dm_version": "2.0",
     "reboot": True, "factory_reset": False, "firmware_download": True, "rollback": "NONE"},
    {"manufacturer": "Huawei", "model_name": "HN8255W", "product_class": "HN8255W",
     "device_kind": "ONT", "oui": "FCFC48", "hardware_versions": ["V1.0"], "family": "TR181", "dm_version": "2.0",
     "reboot": True, "factory_reset": False, "firmware_download": True, "rollback": "DUAL_BANK"},
    {"manufacturer": "ZTE", "model_name": "F670L", "product_class": "F670L",
     "device_kind": "ONT", "oui": "001E73", "hardware_versions": ["V1.0", "V1.2"], "family": "TR098", "dm_version": "1.0",
     "reboot": True, "factory_reset": False, "firmware_download": True, "rollback": "NONE"},
]

_DEFAULT_DATA_MODELS = [
    {"family": "TR098", "version": "1.0", "name": "TR-098 R1.0", "root_path": "InternetGatewayDevice."},
    {"family": "TR181", "version": "2.0", "name": "TR-181 i2.0", "root_path": "Device."},
    {"family": "TR181", "version": "2.1", "name": "TR-181 i2.1", "root_path": "Device."},
]

# code -> (label, data_type, writable, sensitive, sensitive_category, unit)
_DEFAULT_PARAMETER_DEFINITIONS = {
    "DEVICE_SERIAL": ("Device serial", "STRING", False, False, None, None),
    "MANUFACTURER": ("Manufacturer", "STRING", False, False, None, None),
    "MODEL": ("Model", "STRING", False, False, None, None),
    "FIRMWARE_VERSION": ("Firmware version", "STRING", False, False, None, None),
    "HARDWARE_VERSION": ("Hardware version", "STRING", False, False, None, None),
    "UPTIME": ("Uptime", "INTEGER", False, False, None, "seconds"),
    "WAN_STATUS": ("WAN status", "STRING", False, False, None, None),
    "WAN_IP": ("WAN IP", "STRING", False, False, None, None),
    "PPP_USERNAME": ("PPPoE username", "STRING", False, False, None, None),
    "PPP_PASSWORD": ("PPPoE password", "STRING", True, True, "PPPOE_PASSWORD", None),
    "VLAN_ID": ("VLAN id", "INTEGER", True, False, None, None),
    "DNS_SERVERS": ("DNS servers", "STRING", True, False, None, None),
    "WIFI_SSID_24GHZ": ("Wi-Fi SSID 2.4 GHz", "STRING", True, False, None, None),
    "WIFI_PASSWORD_24GHZ": ("Wi-Fi password 2.4 GHz", "STRING", True, True, "WIFI_PASSWORD", None),
    "WIFI_ENABLED_24GHZ": ("Wi-Fi enabled 2.4 GHz", "BOOLEAN", True, False, None, None),
    "WIFI_CHANNEL_24GHZ": ("Wi-Fi channel 2.4 GHz", "INTEGER", True, False, None, None),
    "WIFI_SECURITY_MODE": ("Wi-Fi security mode", "STRING", True, False, None, None),
    "WIFI_SSID_5GHZ": ("Wi-Fi SSID 5 GHz", "STRING", True, False, None, None),
    "WIFI_PASSWORD_5GHZ": ("Wi-Fi password 5 GHz", "STRING", True, True, "WIFI_PASSWORD", None),
    "WIFI_ENABLED_5GHZ": ("Wi-Fi enabled 5 GHz", "BOOLEAN", True, False, None, None),
    "CONNECTED_HOSTS": ("Connected host count", "INTEGER", False, False, None, None),
    "OPTICAL_RX": ("Optical RX power", "FLOAT", False, False, None, "dBm"),
    "OPTICAL_TX": ("Optical TX power", "FLOAT", False, False, None, "dBm"),
    "CPU_USAGE": ("CPU usage", "FLOAT", False, False, None, "percent"),
    "MEMORY_USAGE": ("Memory usage", "FLOAT", False, False, None, "percent"),
    "PERIODIC_INFORM_INTERVAL": ("Periodic Inform interval", "INTEGER", True, False, None, "seconds"),
    "MANAGEMENT_SERVER_URL": ("ACS URL", "STRING", True, False, None, None),
    "CWMP_USERNAME": ("CWMP username", "STRING", True, False, None, None),
    "CWMP_PASSWORD": ("CWMP password", "STRING", True, True, "ACS_CREDENTIAL", None),
    "CONNECTION_REQUEST_PASSWORD": ("Connection-request password", "STRING", True, True, "CONNECTION_REQUEST_CREDENTIAL", None),
    "NTP_SERVERS": ("NTP servers", "STRING", True, False, None, None),
    "TIMEZONE": ("Timezone", "STRING", True, False, None, None),
}

# TR-181 (Device.*) and TR-098 (InternetGatewayDevice.*) paths per model variant.
_TR181_PATHS = {
    "DEVICE_SERIAL": "Device.DeviceInfo.SerialNumber",
    "MANUFACTURER": "Device.DeviceInfo.Manufacturer",
    "MODEL": "Device.DeviceInfo.ModelName",
    "FIRMWARE_VERSION": "Device.DeviceInfo.SoftwareVersion",
    "HARDWARE_VERSION": "Device.DeviceInfo.HardwareVersion",
    "UPTIME": "Device.DeviceInfo.UpTime",
    "WAN_STATUS": "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionStatus",
    "WAN_IP": "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ExternalIPAddress",
    "PPP_USERNAME": "Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Username",
    "PPP_PASSWORD": "Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Password",
    "VLAN_ID": "Device.Ethernet.VLANTermination.1.VLANID",
    "DNS_SERVERS": "Device.IP.Interface.1.IPv4DNSServer.1.DNSServer",
    "WIFI_SSID_24GHZ": "Device.WiFi.SSID.1.SSID",
    "WIFI_PASSWORD_24GHZ": "Device.WiFi.AccessPoint.1.Security.KeyPassphrase",
    "WIFI_ENABLED_24GHZ": "Device.WiFi.SSID.1.Enable",
    "WIFI_CHANNEL_24GHZ": "Device.WiFi.Radio.1.Channel",
    "WIFI_SECURITY_MODE": "Device.WiFi.AccessPoint.1.Security.ModeEnabled",
    "WIFI_SSID_5GHZ": "Device.WiFi.SSID.2.SSID",
    "WIFI_PASSWORD_5GHZ": "Device.WiFi.AccessPoint.2.Security.KeyPassphrase",
    "WIFI_ENABLED_5GHZ": "Device.WiFi.SSID.2.Enable",
    "CONNECTED_HOSTS": "Device.Hosts.HostNumberOfEntries",
    "OPTICAL_RX": "Device.OPTICAL.RxPower",
    "OPTICAL_TX": "Device.OPTICAL.TxPower",
    "CPU_USAGE": "Device.DeviceInfo.ProcessStatus.CPUUsage",
    "MEMORY_USAGE": "Device.DeviceInfo.ProcessStatus.MemoryUsage",
    "PERIODIC_INFORM_INTERVAL": "Device.ManagementServer.PeriodicInformInterval",
    "MANAGEMENT_SERVER_URL": "Device.ManagementServer.URL",
    "CWMP_USERNAME": "Device.ManagementServer.ConnectionRequestUsername",
    "CWMP_PASSWORD": "Device.ManagementServer.ConnectionRequestPassword",
    "CONNECTION_REQUEST_PASSWORD": "Device.ManagementServer.ConnectionRequestPassword",
    "NTP_SERVERS": "Device.Time.NTPServer1",
    "TIMEZONE": "Device.Time.LocalTimeZone",
}

_TR098_PATHS = {
    "DEVICE_SERIAL": "InternetGatewayDevice.DeviceInfo.SerialNumber",
    "MANUFACTURER": "InternetGatewayDevice.DeviceInfo.Manufacturer",
    "MODEL": "InternetGatewayDevice.DeviceInfo.ModelName",
    "FIRMWARE_VERSION": "InternetGatewayDevice.DeviceInfo.SoftwareVersion",
    "HARDWARE_VERSION": "InternetGatewayDevice.DeviceInfo.HardwareVersion",
    "UPTIME": "InternetGatewayDevice.DeviceInfo.UpTime",
    "WAN_STATUS": "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionStatus",
    "WAN_IP": "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ExternalIPAddress",
    "PPP_USERNAME": "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Username",
    "PPP_PASSWORD": "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Password",
    "VLAN_ID": "InternetGatewayDevice.LANDevice.1.LANEthernetInterfaceConfig.1.VID",
    "DNS_SERVERS": "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.DNSServers",
    "WIFI_SSID_24GHZ": "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.SSID",
    "WIFI_PASSWORD_24GHZ": "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.PreSharedKey",
    "WIFI_ENABLED_24GHZ": "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.Enable",
    "WIFI_CHANNEL_24GHZ": "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.Channel",
    "WIFI_SECURITY_MODE": "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.WEPKeyIndex",
    "WIFI_SSID_5GHZ": "InternetGatewayDevice.LANDevice.1.WLANConfiguration.2.SSID",
    "WIFI_PASSWORD_5GHZ": "InternetGatewayDevice.LANDevice.1.WLANConfiguration.2.PreSharedKey",
    "WIFI_ENABLED_5GHZ": "InternetGatewayDevice.LANDevice.1.WLANConfiguration.2.Enable",
    "CONNECTED_HOSTS": "InternetGatewayDevice.LANDevice.1.Hosts.HostNumberOfEntries",
    "PERIODIC_INFORM_INTERVAL": "InternetGatewayDevice.ManagementServer.PeriodicInformInterval",
    "MANAGEMENT_SERVER_URL": "InternetGatewayDevice.ManagementServer.URL",
    "CWMP_USERNAME": "InternetGatewayDevice.ManagementServer.ConnectionRequestUsername",
    "CWMP_PASSWORD": "InternetGatewayDevice.ManagementServer.ConnectionRequestPassword",
    "CONNECTION_REQUEST_PASSWORD": "InternetGatewayDevice.ManagementServer.ConnectionRequestPassword",
    "NTP_SERVERS": "InternetGatewayDevice.Time.NTPServer1",
    "TIMEZONE": "InternetGatewayDevice.Time.LocalTimeZone",
}

_DEFAULT_DIAGNOSTICS = ["PING", "TRACEROUTE", "WAN_STATUS", "CONNECTED_HOSTS", "OPTICAL_RX_TX", "UPTIME", "CPU", "MEMORY"]
_DEFAULT_ACTIONS = [("REBOOT", "Reboot", False), ("FACTORY_RESET", "Reboot", True)]


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def ensure_global_defaults(session: Session) -> None:
    """Seed the model catalogue once (guarded)."""
    if session.scalars(select(DeviceDataModel).limit(1)).first() is None:
        for dm in _DEFAULT_DATA_MODELS:
            session.add(DeviceDataModel(family=dm["family"], version=dm["version"], name=dm["name"],
                                        root_path=dm["root_path"]))
    if session.scalars(select(DeviceManufacturer).limit(1)).first() is None:
        for m in _DEFAULT_MANUFACTURERS:
            session.add(DeviceManufacturer(name=m["name"], oui=m["oui"], is_active=True))
        session.flush()
    if session.scalars(select(ParameterDefinition).limit(1)).first() is None:
        for code, (label, data_type, writable, sensitive, category, unit) in _DEFAULT_PARAMETER_DEFINITIONS.items():
            session.add(ParameterDefinition(code=code, label=label, data_type=data_type, writable=writable,
                                            sensitive=sensitive, sensitive_category=category, unit=unit))
    if session.scalars(select(DeviceModel).limit(1)).first() is None:
        _seed_models(session)
    session.flush()


def _seed_models(session: Session) -> None:
    manufacturers = {m.name: m for m in session.scalars(select(DeviceManufacturer))}
    for spec in _DEFAULT_MODELS:
        manufacturer = manufacturers.get(spec["manufacturer"])
        model = DeviceModel(manufacturer_id=manufacturer.id, model_name=spec["model_name"],
                            oui=spec["oui"], product_class=spec["product_class"],
                            device_kind=spec["device_kind"], is_active=True)
        session.add(model)
        session.flush()
        for hw in spec["hardware_versions"]:
            variant = DeviceModelVariant(model_id=model.id, hardware_version=hw,
                                         data_model_family=spec["family"], data_model_version=spec["dm_version"],
                                         reboot_supported=spec["reboot"],
                                         factory_reset_supported=spec["factory_reset"],
                                         firmware_download_supported=spec["firmware_download"],
                                         rollback_capability=spec["rollback"])
            session.add(variant)
            session.flush()
            path_table = _TR181_PATHS if spec["family"] == "TR181" else _TR098_PATHS
            for code, path in path_table.items():
                definition = session.scalars(
                    select(ParameterDefinition).where(ParameterDefinition.code == code)).first()
                if definition is None:
                    continue
                session.add(ParameterMapping(definition_id=definition.id, model_variant_id=variant.id,
                                             path=path, read_path=path, data_model_family=spec["family"],
                                             writable=definition.writable, mapping_version=1))
            for diag in _DEFAULT_DIAGNOSTICS:
                session.add(SupportedDiagnostic(model_variant_id=variant.id, diagnostic=diag))
            for action, rpc, elevated in _DEFAULT_ACTIONS:
                session.add(SupportedAction(model_variant_id=variant.id, action=action, rpc_name=rpc,
                                            requires_elevated_permission=elevated))
    session.flush()


def resolve_model_variant(session: Session, *, oui: str | None = None, product_class: str | None = None,
                          model_name: str | None = None, hardware_version: str | None = None,
                          data_model_family: str | None = None) -> DeviceModelVariant | None:
    """Match a device to a known model variant. Returns None when unknown."""
    query = select(DeviceModelVariant).join(DeviceModel, DeviceModel.id == DeviceModelVariant.model_id)
    if product_class:
        query = query.where(DeviceModel.product_class == product_class)
    elif model_name:
        query = query.where(DeviceModel.model_name == model_name)
    if hardware_version:
        query = query.where(DeviceModelVariant.hardware_version == hardware_version)
    if data_model_family:
        query = query.where(DeviceModelVariant.data_model_family == data_model_family)
    return session.scalars(query.limit(1)).first()


def mappings_for_variant(session: Session, variant_id: uuid.UUID) -> list[dict]:
    rows = list(session.scalars(select(ParameterMapping).where(ParameterMapping.model_variant_id == variant_id)))
    return [{"code": _code_for(session, r.definition_id), "path": r.path, "read_path": r.read_path,
             "data_model_family": r.data_model_family, "writable": r.writable,
             "mapping_version": r.mapping_version} for r in rows]


def _code_for(session: Session, definition_id: uuid.UUID) -> str:
    definition = session.get(ParameterDefinition, definition_id)
    return definition.code if definition else str(definition_id)


def sensitive_definitions(session: Session) -> dict[str, str]:
    return {d.code: (d.sensitive_category or "OTHER_SECRET")
            for d in session.scalars(select(ParameterDefinition).where(ParameterDefinition.sensitive.is_(True)))}


def sensitive_paths_for_variant(session: Session, variant_id: uuid.UUID) -> list[str]:
    """Device paths that correspond to sensitive parameter definitions."""
    rows = list(session.scalars(select(ParameterMapping).join(
        ParameterDefinition, ParameterDefinition.id == ParameterMapping.definition_id).where(
        ParameterMapping.model_variant_id == variant_id, ParameterDefinition.sensitive.is_(True))))
    return [r.path for r in rows]


def capability_snapshot_for(session: Session, variant_id: uuid.UUID) -> dict:
    """Build the capability snapshot for a model variant (confirmed from the
    catalogue, marked UNVERIFIED until refreshed from the device)."""
    capabilities = list(session.scalars(select(DeviceCapability).where(DeviceCapability.model_variant_id == variant_id)))
    diagnostics = list(session.scalars(select(SupportedDiagnostic).where(SupportedDiagnostic.model_variant_id == variant_id)))
    actions = list(session.scalars(select(SupportedAction).where(SupportedAction.model_variant_id == variant_id)))
    writable = [m["code"] for m in mappings_for_variant(session, variant_id) if m["writable"]]
    return {
        "state": "INFERRED",
        "parameters": [c.name for c in capabilities],
        "diagnostics": [d.diagnostic for d in diagnostics],
        "actions": [a.action for a in actions],
        "writable_parameters": writable,
        "firmware_operations": ["DOWNLOAD"],
    }


def audit_seeded(session: Session) -> None:
    session.flush()
