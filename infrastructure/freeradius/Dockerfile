FROM freeradius/freeradius-server:3.2.5-alpine

RUN apk add --no-cache python3

COPY infrastructure/freeradius/clients.conf /etc/raddb/clients.conf
COPY infrastructure/freeradius/backend_auth /etc/raddb/mods-available/backend_auth
COPY infrastructure/freeradius/backend_accounting /etc/raddb/mods-available/backend_accounting
COPY infrastructure/freeradius/default /etc/raddb/sites-available/default
COPY infrastructure/freeradius/aaa_adapter.py /usr/local/bin/aaa_adapter.py
COPY infrastructure/freeradius/entrypoint.sh /usr/local/bin/freeradius-entrypoint

RUN chmod 0755 /usr/local/bin/aaa_adapter.py /usr/local/bin/freeradius-entrypoint \
    && ln -sf ../mods-available/backend_auth /etc/raddb/mods-enabled/backend_auth \
    && ln -sf ../mods-available/backend_accounting /etc/raddb/mods-enabled/backend_accounting

ENTRYPOINT ["/usr/local/bin/freeradius-entrypoint"]
CMD ["radiusd"]
