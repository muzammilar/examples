FROM starrocks/be-ubuntu:4.1.7
# Explicit byte budget below the 2 GiB container cap, not host-RAM sizing.
RUN printf '\nstorage_root_path = /data\nmem_limit = 1024M\nsync_tablet_meta = true\n' >> /opt/starrocks/be/conf/be.conf \
 && mkdir -p /data
ENV HOST_TYPE=FQDN LOG_CONSOLE=1
CMD ["/bin/bash", "/opt/starrocks/be_entrypoint.sh", "sr-fe-0"]
