FROM apache/spark:4.0.1-python3
USER root
ADD https://repo.maven.apache.org/maven2/org/apache/iceberg/iceberg-spark-runtime-4.0_2.13/1.10.1/iceberg-spark-runtime-4.0_2.13-1.10.1.jar /opt/spark/jars/iceberg-runtime.jar
ADD https://repo.maven.apache.org/maven2/org/apache/iceberg/iceberg-aws-bundle/1.10.1/iceberg-aws-bundle-1.10.1.jar /opt/spark/jars/iceberg-aws.jar
RUN echo '69ab79b02537e9a865556ded766137ecca33613ef72b6f94659174135614dc095556f3df187d54e1d66388e65557e0cc26ba3dab53ac0a3f7163cf7afc7e13e4  /opt/spark/jars/iceberg-runtime.jar' | sha512sum -c - \
 && echo 'a649f50fd8508b3e179002ecbc28b3ae3de374c6851ce2ed203fe29d3bbf7794780075bd8ad8f41655d4f8684ff064f58d32218d329b0937bce034199afb900a  /opt/spark/jars/iceberg-aws.jar' | sha512sum -c - \
 && chmod 644 /opt/spark/jars/iceberg-*.jar
ARG LOCAL_UID=10001
RUN if ! getent passwd "${LOCAL_UID}" >/dev/null; then useradd --no-create-home --uid "${LOCAL_UID}" lab; fi
COPY Dockerfile compact.py resources.py test_compactor.py test_compactor_live.py /opt/lab/
RUN cd /opt/lab && sha256sum Dockerfile compact.py resources.py test_compactor.py test_compactor_live.py > sources.sha256
USER spark
ENV SPARK_USER=lab HADOOP_USER_NAME=lab
ENTRYPOINT ["/opt/spark/bin/spark-submit", "--master", "local[2]", "--driver-memory", "1g", "--conf", "spark.jars.ivy=/tmp/ivy", "--conf", "spark.driver.extraJavaOptions=-Duser.home=/tmp", "/opt/lab/compact.py"]
