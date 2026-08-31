# Security domains

Platform authentication authenticates application operators. AAA authenticates
subscribers for network access. They are separate credential and security
domains. A RADIUS username/password, NAS secret or internal RADIUS key cannot
be used against `/api/v1/auth`; a platform JWT cannot be used in a RADIUS
Access-Request or as a NAS shared secret.
