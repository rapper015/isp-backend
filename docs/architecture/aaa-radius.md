# AAA and RADIUS

AAA owns subscriber credentials, NAS shared secrets, accounting and RADIUS
policy in the `aaa` database. Network/RADIUS contracts use scoped internal
credentials on the private Docker network. Management APIs accept only valid
platform access tokens with explicit `aaa.*` permissions. Platform passwords
and JWTs never authenticate subscriber network access.
