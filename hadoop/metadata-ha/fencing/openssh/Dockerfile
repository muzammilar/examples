FROM examples/hadoop-metadata-ha:3.5.0
COPY fencing/openssh/fence.sh /opt/hadoop/fence.sh
RUN chmod 755 /opt/hadoop/fence.sh && python3 -c "import xml.etree.ElementTree as E; p='/opt/hadoop/etc/hadoop/hdfs-site.xml'; t=E.parse(p); next(x.find('value') for x in t.getroot() if x.findtext('name')=='dfs.ha.fencing.methods').text='shell(/opt/hadoop/fence.sh)'; t.write(p)"
