# MikroTik NAS onboarding and management

## Architecture

Router communication is server-side only:

```text
Frontend wizard -> Django REST API -> RouterOS binary API/API-SSL
                                  -> database-backed FreeRADIUS client registry
```

The existing `network.NasDevice` remains the canonical NAS record. Its legacy integer primary key and `/api/v1/network/nas` routes remain compatible; new management APIs expose `public_id` as the UUID `id`. `FreeRadiusClient` stores the dynamic client definition used by this REST-oriented FreeRADIUS integration. `NasAuditLog` stores redacted mutations, and `cached_health` prevents user-facing GET requests from polling routers.

FreeRADIUS can resolve synchronized clients through `POST /internal/aaa/nas-client` using the existing `x-internal-api-key` contract and a body such as `{"source_ip":"10.30.0.1"}`. The shared secret is decrypted only for this protected internal route and is never available under `/api/v1`. Configure an `rlm_rest` dynamic-client lookup to call it. A deployment based on static `clients.conf` needs an external export/reload step; Django intentionally does not restart an unmanaged FreeRADIUS daemon.

No Celery installation exists. Run `manage.py check_nas_health` from cron/systemd/Kubernetes CronJob to refresh cached health. It uses short connections and staggers routers.

## Environment

```dotenv
# Required: stable Fernet key; changing it makes stored credentials unreadable.
NAS_ENCRYPTION_KEY=<Fernet key>

# Private router ranges reachable from the Django server. A resolved private IP
# outside these ranges is rejected. Loopback/link-local/reserved IPs are always rejected.
NAS_ALLOWED_NETWORKS=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
NAS_ALLOW_PRIVATE_NETWORKS=true

# Keep false in production. Setting true only permits a request to use verify_tls=false.
NAS_ALLOW_INSECURE_TLS=false
RADIUS_SERVER_IP=10.0.0.10
```

Generate the encryption key once:

```powershell
py -3.12 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Do not rotate it without decrypting and re-encrypting stored secrets. Neither plaintext nor ciphertext appears in NAS API responses, audits, or application logs.

## Permissions and tenant isolation

`super_admin` can manage every franchise. `noc_admin` can manage only franchises assigned through `AdminUser.franchises`. Other roles cannot access NAS management. All stored-NAS lookups use the same scoped queryset.

## Frontend wizard contract

1. **Connection Details:** host, explicit `api_protocol` (`API` or `API_SSL`), explicit port, username, password, TLS verification, optional CA/fingerprint and timeout.
2. **Test Connection:** call `POST /api/v1/nas/test-connection/`. It performs no database or router mutation.
3. **Router Discovery:** call `POST /api/v1/nas/discover/`. Show identity/resources, interfaces, addresses, current RADIUS/PPP AAA, PPPoE, Hotspot and pool data.
4. **RADIUS Configuration:** collect franchise, NAS name/short name, actual RADIUS source IP, secret, ports, and only the selected services: `ppp`, `pppoe`, `hotspot`, `login`, `wireless`, `dhcp`.
5. **Confirm and save:** call `POST /api/v1/nas/` with `confirm: true`. Django tests/discovers again, encrypts credentials, stores the NAS, and synchronizes its FreeRADIUS client.
6. **Preview:** `GET /api/v1/nas/{id}/configuration-preview/` reads existing router configuration and returns CREATE/UPDATE plus desired values. Secrets are absent.
7. **Confirm and apply:** `POST /api/v1/nas/{id}/configure-radius/` with `{"confirm": true}`. This updates or creates only the matching RADIUS server, configures PPP/Hotspot only when selected, enables accounting/interim updates and RADIUS incoming CoA, then reads back and audits the result.
8. **Verification:** display the apply result and cached `/health/`; trigger `/sync/` when an explicit live refresh is wanted.

`pppoe` is translated to RouterOS's `ppp` RADIUS service. Unrelated `/radius` entries are never deleted, and an entry with the configured RADIUS server address is updated rather than duplicated.

## Endpoints

All endpoints require the admin bearer token.

- `POST /api/v1/nas/test-connection/`
- `POST /api/v1/nas/discover/`
- `GET|POST /api/v1/nas/`
- `GET|PATCH|DELETE /api/v1/nas/{uuid}/`
- `POST /api/v1/nas/{uuid}/test-connection/`
- `POST /api/v1/nas/{uuid}/sync/`
- `GET /api/v1/nas/{uuid}/configuration-preview/`
- `POST /api/v1/nas/{uuid}/configure-radius/`
- `GET /api/v1/nas/{uuid}/health/`
- `GET /api/v1/nas/{uuid}/interfaces/`
- `GET /api/v1/nas/{uuid}/ip-addresses/`
- `GET /api/v1/nas/{uuid}/radius/`
- `GET /api/v1/nas/{uuid}/pppoe-servers/`
- `GET /api/v1/nas/{uuid}/hotspot-servers/`
- `GET /api/v1/nas/{uuid}/ip-pools/`
- `GET /api/v1/nas/{uuid}/active-sessions/`
- `POST /api/v1/nas/{uuid}/disconnect-session/`
- `GET /api/v1/nas/{uuid}/audit-logs/`

Connection tests are throttled to 10 per admin/IP per hour. Safe error responses contain an application code such as `CONNECTION_TIMEOUT`, `CONNECTION_REFUSED`, `AUTHENTICATION_FAILED`, `INSUFFICIENT_PERMISSION`, `TLS_FAILED`, or an SSRF-policy code. Raw socket/TLS/RouterOS exceptions are not returned.

### Test connection

```bash
curl -X POST http://localhost:4000/api/v1/nas/test-connection/ \
  -H "Authorization: Bearer TOKEN" -H "Content-Type: application/json" \
  -d '{"host":"10.20.0.1","api_port":8729,"api_protocol":"API_SSL","api_username":"isp-app","api_password":"REDACTED","verify_tls":true,"connection_timeout":5}'
```

### Confirm NAS

```json
{
  "confirm": true,
  "franchise_id": 1,
  "name": "Main CCR",
  "short_name": "main-ccr",
  "host": "10.20.0.1",
  "api_port": 8729,
  "api_protocol": "API_SSL",
  "api_username": "isp-app",
  "api_password": "REDACTED",
  "verify_tls": true,
  "radius_source_ip": "10.30.0.1",
  "radius_secret": "REDACTED",
  "radius_auth_port": 1812,
  "radius_accounting_port": 1813,
  "coa_port": 3799,
  "radius_services": ["pppoe"]
}
```

Typical safe response:

```json
{"id":"18ac889f-dd2d-402d-884d-83b508cb43c9","name":"Main CCR","host":"10.20.0.1","api_protocol":"API_SSL","lifecycle_status":"ONLINE","enabled":true}
```

## One-time RouterOS setup

Prefer a CA-signed or privately trusted certificate and `api-ssl`; port 8293 is not assumed. Restrict the service to the Django server/VPN source address.

```routeros
/user group add name=isp-app-group policy=api,read,write
/user add name=isp-app group=isp-app-group password="USE-A-UNIQUE-LONG-PASSWORD"
/ip service set api disabled=yes
/ip service set api-ssl disabled=no port=8729 address=<DJANGO-SERVER-IP>/32 certificate=<CERTIFICATE-NAME>
```

The implemented operations need `api`, `read`, and `write`. Do not add `policy`, `sensitive`, `password`, `ftp`, `reboot`, `sniff`, or broad administrative policies. Add `test` only if a separately implemented diagnostic later requires it. Never use the main RouterOS administrator account.

The Django server must be able to reach the selected API port. The router must be able to reach `RADIUS_SERVER_IP` ports 1812/1813, and FreeRADIUS/Django infrastructure must reach the router's selected CoA port 3799. Router management IP, RADIUS source IP, RADIUS server IP, and subscriber addresses are intentionally distinct fields.

## Health scheduling and tests

```powershell
py -3.12 manage.py migrate
py -3.12 manage.py check_nas_health --stagger-seconds 0.25
py -3.12 manage.py test network
py -3.12 manage.py test
```

Tests use mocked RouterOS clients and never contact a production router.
