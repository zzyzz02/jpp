ARG ODOO_VERSION=20.0
FROM odoo:${ODOO_VERSION}

USER root
COPY requirements.txt /tmp/requirements.txt
# Debian 12+ (PEP 668) blocks system pip; image Odoo memang pakai system python.
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

COPY --chown=odoo:odoo custom_addons /mnt/extra-addons
COPY --chown=odoo:odoo odoo.conf /etc/odoo/odoo.conf
USER odoo
