# RouterOS Integration Guide

## Replaceable adapter

`app/routeros.py` defines a `RouterOSAdapter` interface that is the only way the
backend talks to a router. Every method:

* accepts typed inputs and validates values
* enforces connection and command timeouts
* returns normalized results
* redacts secrets
* raises structured exceptions (`RouterOSError` subclasses with stable codes)
* never accepts a free-form RouterOS command string

There is **no general-purpose RouterOS command terminal** and frontend users can
never send arbitrary RouterOS commands.

Implementations:

* `RouterOSApiAdapter` — real adapter backed by the maintained `routeros_api`
  library (RouterOS binary API, API-SSL, custom ports, IPv4/IPv6, TLS
  verification, timeouts).
* `RouterOSV6Adapter` / `RouterOSV7Adapter` — version-specific behavior (package
  paths, login mode) confined to subclasses.
* `FakeRouterOSAdapter` — deterministic in-memory router for tests and safe
  simulations. The test suite uses it; it never touches a physical router.

`build_adapter()` in `app/nas_service.py` selects the adapter. Set
`AAA_ROUTEROS_ADAPTER=fake` only for tests/simulations.

## Supported versions

* RouterOS v6 (6.43+ challenge login)
* RouterOS v7 (legacy binary API boundary)

Unsupported or unknown versions fail with `UNSUPPORTED_ROUTEROS_VERSION` and the
NAS is never marked connected.

## API versus API-SSL

* `management_protocol=api` → RouterOS API on the configured port (default 8728).
* `management_protocol=api_ssl` → RouterOS API over TLS (default 8729). TLS
  verification is enforced unless `tls_verify=false` is explicitly allowed by
  policy (`AAA_ALLOW_INSECURE_TLS`). Prefer CA-signed or privately trusted
  certificates.

## Required RouterOS user permissions (least privilege)

Create a dedicated RouterOS user, never the main administrator account:

```routeros
/user group add name=isp-app-group policy=api,read,write
/user add name=isp-app group=isp-app-group password="USE-A-UNIQUE-LONG-PASSWORD"
/ip service set api disabled=yes
/ip service set api-ssl disabled=no port=8729 address=<BACKEND-SERVER-IP>/32 certificate=<CERTIFICATE-NAME>
```

The implemented operations need `api`, `read` and `write` only. Do **not** grant
`policy`, `sensitive`, `password`, `ftp`, `reboot`, `sniff` or broad
administrative policies. `test` is only needed if a separate diagnostic requires
it.

The integration must never:

* delete local break-glass administrator accounts
* disable all local login methods
* change unrelated firewall rules, routing tables, interfaces, PPP users,
  Hotspot users or queues
* upgrade RouterOS firmware
* reboot the router

## Capability detection

`detect_capabilities()` returns normalized flags for: RouterOS API, API-SSL, PPP,
PPPoE, Hotspot, router login AAA, Wireless, CAPsMAN, DHCP RADIUS, Dot1X,
Accounting, interim accounting, incoming CoA, Disconnect-Request, address lists,
static IP, IPv6, vendor-specific attributes, and Message-Authenticator options.
Baseline flags come from the version; menu probes refine them at discovery time.
Results are stored in `NasCapability` and exposed via
`GET /api/nas/{id}/capabilities`.

## Services managed

RADIUS services the backend may configure (only the ones the administrator
selects): `ppp`, `pppoe`, `hotspot`, `login`, `wireless`, `dhcp`, `ipsec`,
`dot1x`. `pppoe` maps to RouterOS's `ppp` RADIUS service. Services are never
enabled automatically.

### PPP / PPPoE AAA

`ppp_aaa` manages `use-radius`, `accounting`, and `interim-update` (interim
interval validated against tenant policy, min 60s). The backend does **not**
create PPPoE servers as part of basic onboarding; that is a separate explicit
workflow.

### Hotspot AAA

The backend reads Hotspot profiles and enables RADIUS only on the explicitly
selected profiles (`use-radius`, `radius-accounting`, interim interval, optional
location metadata). Unrelated profiles are preserved.

### Router administrative login (user AAA)

Disabled by default. Enabling `login_radius` requires:

* explicit selection
* elevated permission
* break-glass local administrator verification (`break_glass_verified`)
* risk acknowledgement (`acknowledge_login_risk`)
* a default group and excluded groups
* immediate rollback and audit of every change

This is a `critical` change and requires approval.

### Incoming CoA / Disconnect

Where supported, the backend configures `/radius/incoming` (`accept`, port,
source restrictions) and coordinates with the existing Python CoA/Disconnect
adapter. Tracked states: Configured, Reachable, ACK received, NAK received,
Timed out, Unsupported.

### Accounting

Accounting enablement, interim interval, selected services, start/stop and
interim verification, and last-accounting-time health. Intervals are validated
against platform policy to avoid overloading the NAS/network/backend.

## Verification

After applying, the backend re-reads the relevant state and compares desired vs
actual (secret-free). A checksummed, redacted snapshot is stored and remote
object IDs tracked. If verification fails, the NAS never advances to ACTIVE and
rollback may run automatically where safe.
