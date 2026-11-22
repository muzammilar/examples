"""Render this example's fixed three-shard, three-replica topology."""
from pathlib import Path
from decimal import Decimal
import os
from xml.etree import ElementTree as ET
data_cpus = Decimal(os.environ.get('CLUSTER_DATA_CPUS', '0.5'))
if not data_cpus.is_finite() or not 0 < data_cpus <= 4:
    raise ValueError('CLUSTER_DATA_CPUS must be finite and greater than zero, at most 4')
out=Path(__file__).resolve().parents[1]/'config'/'generated'
out.mkdir(parents=True,exist_ok=True)
remote=ET.Element('clickhouse')
cluster=ET.SubElement(ET.SubElement(remote,'remote_servers'),'lake_serving')
for shard in range(1,4):
    s=ET.SubElement(cluster,'shard');ET.SubElement(s,'internal_replication').text='true'
    for replica in range(1,4):
        r=ET.SubElement(s,'replica')
        for k,v in {'host':f'ch{shard}{replica}','port':'9000','user':'default','password':'local-reader'}.items(): ET.SubElement(r,k).text=v
ET.indent(remote);ET.ElementTree(remote).write(out/'remote.xml',encoding='unicode')
services=['services:']
for shard in range(1,4):
    for replica in range(1,4):
        name=f'ch{shard}{replica}'
        cfg=ET.Element('clickhouse');macros=ET.SubElement(cfg,'macros')
        ET.SubElement(macros,'shard').text=str(shard);ET.SubElement(macros,'replica').text=name
        zk=ET.SubElement(cfg,'zookeeper')
        for i in range(1,4):
            node=ET.SubElement(zk,'node');ET.SubElement(node,'host').text=f'keeper{i}';ET.SubElement(node,'port').text='9181'
        ET.indent(cfg);ET.ElementTree(cfg).write(out/f'{name}.xml',encoding='unicode')
        services.append(f'''  {name}:
    profiles: [cluster]
    image: clickhouse/clickhouse-server:26.10.2.103
    hostname: {name}
    environment:
      CLICKHOUSE_PASSWORD: local-reader
    volumes:
      - ./config/clickhouse.xml:/etc/clickhouse-server/config.d/limits.xml:ro
      - ./config/generated/{name}.xml:/etc/clickhouse-server/config.d/topology.xml:ro
      - {name}:/var/lib/clickhouse
    mem_limit: 1280m
    memswap_limit: 1280m
    cpus: {data_cpus}
    healthcheck:
      test: [CMD, clickhouse-client, --password, local-reader, --query, 'SELECT 1']
      interval: 3s
      timeout: 5s
      retries: 60
''')
for i in range(1,4):
    root=ET.Element('clickhouse');logger=ET.SubElement(root,'logger');ET.SubElement(logger,'level').text='warning'
    keeper=ET.SubElement(root,'keeper_server')
    for k,v in {'tcp_port':'9181','server_id':str(i),'log_storage_path':'/var/lib/clickhouse/coordination/log','snapshot_storage_path':'/var/lib/clickhouse/coordination/snapshots'}.items():ET.SubElement(keeper,k).text=v
    ET.SubElement(keeper,'four_letter_word_white_list').text='ruok,mntr,srvr'
    raft=ET.SubElement(keeper,'raft_configuration')
    for j in range(1,4):
        server=ET.SubElement(raft,'server')
        for k,v in {'id':str(j),'hostname':f'keeper{j}','port':'9234'}.items():ET.SubElement(server,k).text=v
    ET.SubElement(root,'listen_host').text='0.0.0.0'
    ET.indent(root);ET.ElementTree(root).write(out/f'keeper{i}.xml',encoding='unicode')
    services.append(f'''  keeper{i}:
    profiles: [cluster]
    image: clickhouse/clickhouse-server:26.10.2.103
    entrypoint: [clickhouse-keeper, --config-file=/etc/lab/keeper.xml]
    volumes:
      - ./config/generated/keeper{i}.xml:/etc/lab/keeper.xml:ro
      - keeper{i}:/var/lib/clickhouse
    mem_limit: 256m
    memswap_limit: 256m
    cpus: 0.5
    healthcheck:
      test: [CMD, clickhouse-keeper-client, --host, '127.0.0.1', --port, '9181', --query, ruok]
      interval: 3s
      timeout: 5s
      retries: 60
''')
services+=['volumes:']+[f'  ch{s}{r}:' for s in range(1,4) for r in range(1,4)]+[f'  keeper{i}:' for i in range(1,4)]
(out/'cluster.yml').write_text('\n'.join(services)+'\n')
