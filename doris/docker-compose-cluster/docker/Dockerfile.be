FROM apache/doris:be-4.0.8@sha256:d911ae3826491598be4e383cba83011a266091b8fdc6c34c036280220cc4fea9
RUN printf '\nmem_limit=2G\nnum_cores=1\n' >> /opt/apache-doris/be/conf/be.conf
