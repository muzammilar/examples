FROM apache/doris:be-4.1.5@sha256:10d282e1bd685f8c0d71b3b42bbd0841bbb20909de2cd847923f00378b6b9a11
RUN printf '\nmem_limit=2304M\nnum_cores=1\n' >> /opt/apache-doris/be/conf/be.conf
RUN sed -i 's/percpu_arena:percpu/percpu_arena:disabled,narenas:2,tcache_max:4096/' /opt/apache-doris/be/conf/be.conf
