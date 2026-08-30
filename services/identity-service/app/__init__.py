"""Identity service: application users + login/token issuance.

This is the platform's IAM service for the people who USE the application
(admins, NOC, sales, KYC, ...). It is intentionally separate from the AAA
service, which owns FreeRADIUS/NAS subscriber authentication."""
