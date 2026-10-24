ARG ASTERISK_IMAGE=andrius/asterisk:22
FROM ${ASTERISK_IMAGE}

USER root
RUN apt-get update -qq \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq gettext-base \
    && rm -rf /var/lib/apt/lists/*

COPY templates/ /opt/fernruf/templates/
COPY entrypoint.sh /opt/fernruf/entrypoint.sh
RUN chmod +x /opt/fernruf/entrypoint.sh \
    && mkdir -p /etc/asterisk/generated /var/lib/asterisk/sounds/custom

ENTRYPOINT ["/opt/fernruf/entrypoint.sh"]
