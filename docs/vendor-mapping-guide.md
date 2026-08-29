# Vendor Mapping Guide — Device Management (Milestone 7)

Vendor-specific TR-069 paths live **only** in the versioned device-model
catalogue (`app/services/catalog_service.py`), never in views or task bodies.
A vendor-neutral profile is compiled against a model variant's parameter
mappings into concrete TR-098/TR-181 paths.

## 1. Supported device models (global defaults seeded at startup)

| Manufacturer | OUI | Model | Product class | Data model | Rollback |
| --- | --- | --- | --- | --- | --- |
| FiberHome | `A4B1C1` | AN5506-04-F1 | AN5506 | TR-181 (2.0) | NONE |
| Huawei | `FCFC48` | HN8255W | HN8255W | TR-181 (2.0) | DUAL_BANK |
| ZTE | `001E73` | F670L | F670L | TR-098 (1.0) | NONE |

These are defaults that `catalog_service.ensure_global_defaults()` seeds on
startup. Real fleets can add their own manufacturers/models/variants/mappings
through the same tables; the service reads the catalogue at compile time.

## 2. Parameter definitions (vendor-neutral codes)

The catalogue defines a stable set of **codes** used by profiles, plus the
TR-181/TR-098 paths each maps to. Examples:

| Code | TR-181 path | TR-098 path | Sensitive |
| --- | --- | --- | --- |
| `DEVICE_SERIAL` | `Device.DeviceInfo.SerialNumber` | `InternetGatewayDevice.DeviceInfo.SerialNumber` | no |
| `FIRMWARE_VERSION` | `Device.DeviceInfo.SoftwareVersion` | `InternetGatewayDevice.DeviceInfo.SoftwareVersion` | no |
| `WIFI_SSID_24GHZ` | `Device.WiFi.SSID.1.SSID` | `InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.SSID` | no |
| `WIFI_PASSWORD_24GHZ` | `Device.WiFi.AccessPoint.1.Security.KeyPassphrase` | `…WLANConfiguration.1.PreSharedKey.1.KeyPassphrase` | **yes** |
| `WIFI_PASSWORD_5GHZ` | `Device.WiFi.AccessPoint.2.Security.KeyPassphrase` | `…WLANConfiguration.2.PreSharedKey.1.KeyPassphrase` | **yes** |
| `VLAN_ID` | `Device.Ethernet.VLANTermination.1.VLANID` | — | no |
| `PERIODIC_INFORM_INTERVAL` | `Device.ManagementServer.PeriodicInformInterval` | `InternetGatewayDevice.ManagementServer.PeriodicInformInterval` | no |
| `PPP_USERNAME` | `Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Username` | `…WANPPPConnection.1.Username` | no |
| `PPP_PASSWORD` | `…WANPPPConnection.1.Password` | `…WANPPPConnection.1.Password` | **yes** |
| `CWMP_PASSWORD` | `Device.ManagementServer.ConnectionRequestPassword` | `…ManagementServer.ConnectionRequestPassword` | **yes** |

Full list: `_DEFAULT_PARAMETER_DEFINITIONS` / `_TR181_PATHS` / `_TR098_PATHS`
in `catalog_service.py` (30 definitions, 3 families of mappings).

## 3. Adding a new vendor/model

1. Insert a `DeviceManufacturer` (OUI) and `DeviceModel` (model name, product
   class, device kind).
2. Insert `DeviceModelVariant`s (hardware version, data-model family/version,
   reboot/factory-reset/firmware-download support, **rollback capability**).
3. Insert `ParameterMapping` rows: for each parameter code this variant
   supports, the concrete read/write path.
4. Add `SupportedAction` / `SupportedDiagnostic` rows so capability checks are
   accurate (a diagnostic the hardware cannot run is flagged `UNSUPPORTED`,
   not silently executed).
5. Add `DeviceCapability` rows (Wi-Fi 5 GHz present? optical Rx/Tx? …) — used
   for capability-aware diagnostics.

> **Rollback honesty**: never set `rollback_capability` to `DUAL_BANK` unless
> the hardware genuinely has dual-bank firmware. The service refuses to claim
> rollback (`rollback_claim_supported`) for anything else, and post-upgrade
> verification failures on unsupported hardware are marked failed — not
> "rolled back".

## 4. Compilation semantics

- `compile_parameters(mappings, definition, data_model_family)` maps each code
  to the variant's concrete path.
- Codes with **no mapping** for the chosen variant are returned as
  `unsupported` and block job creation (a profile that references them cannot
  be applied to that device).
- `compile-preview` lets you test a version against a variant before approval,
  returning both `compiled` paths and `unsupported` codes.

## 5. Sensitive parameters

Sensitive codes/categories (`PPPOE_PASSWORD`, `WIFI_PASSWORD`,
`ACS_CREDENTIAL`, `CONNECTION_REQUEST_CREDENTIAL`) are:

- stored only as **encrypted references** (`secret_ref`), never plaintext;
- masked in logs and never returned by APIs;
- **exempt from read-back verification** (they are unreadable by design) so a
  missing read-back of a secret is not treated as drift;
- redacted from drift mismatches where applicable (`sensitive_paths`).
