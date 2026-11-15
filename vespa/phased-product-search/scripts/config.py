import os
from pathlib import Path
import shutil

mode = os.environ.get("MODE", "cluster")
if mode not in ("single", "cluster", "sharded"):
    raise ValueError("MODE must be single, cluster or sharded")
n = 1 if mode == "single" else 3
aliases = [f"node{i}" for i in range(n)]
if mode == "sharded":
    aliases += [f"content{i}" for i in range(9)]
out = Path("application/generated")
(out / "schemas").mkdir(parents=True, exist_ok=True)
shutil.copyfile("application/schemas/product.sd", out / "schemas/product.sd")
(out / "hosts.xml").write_text(
    "<hosts>"
    + "".join(
        f'<host name="vespa-products-{alias}.vespa-product-search-net"><alias>{alias}</alias></host>'
        for alias in aliases
    )
    + "</hosts>"
)
configs = "".join(f'<configserver hostalias="node{i}"/>' for i in range(n))
controllers = "".join(
    f'<cluster-controller hostalias="node{i}" jvm-options="-Xms32M -Xmx128M"/>'
    for i in range(n)
)
slobroks = "".join(f'<slobrok hostalias="node{i}"/>' for i in range(n))
nodes = "".join(f'<node hostalias="node{i}"/>' for i in range(n))
content = "".join(
    f'<node hostalias="node{i}" distribution-key="{i}"/>' for i in range(n)
)
if mode == "sharded":
    groups = "".join(
        f'<group name="copy{group}" distribution-key="{group}">'
        + "".join(
            f'<node hostalias="content{i}" distribution-key="{i}"/>'
            for i in range(group * 3, group * 3 + 3)
        )
        + "</group>"
        for group in range(3)
    )
    topology = (
        '<group name="root"><distribution partitions="1|1|*"/>' + groups + "</group>"
    )
else:
    topology = f"<nodes>{content}</nodes>"
tuning = (
    "<tuning><min-node-ratio-per-group>1</min-node-ratio-per-group></tuning>"
    if mode == "sharded"
    else ""
)
(out / "services.xml").write_text(f"""<services version="1.0">
 <admin version="2.0">
  <configservers>{configs}</configservers>
  <cluster-controllers>{controllers}</cluster-controllers>
  <slobroks>{slobroks}</slobroks><adminserver hostalias="node0"/>
 </admin>
 <container id="api" version="1.0">
  <search/><document-api/>
  <nodes><jvm options="-Xms64M -Xmx512M"/>{nodes}</nodes>
 </container>
 <content id="catalog" version="1.0">
  <min-redundancy>{n}</min-redundancy>
  <documents><document type="product" mode="index"/></documents>
  <engine><proton><searchable-copies>{n}</searchable-copies></proton></engine>
  {topology}
  {tuning}
 </content>
</services>""")
