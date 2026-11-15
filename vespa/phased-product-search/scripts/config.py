import os
from pathlib import Path
import shutil

mode = os.environ.get("MODE", "cluster")
if mode not in ("single", "cluster"):
    raise ValueError("MODE must be single or cluster")
n = 1 if mode == "single" else 3
out = Path("application/generated")
(out / "schemas").mkdir(parents=True, exist_ok=True)
shutil.copyfile("application/schemas/product.sd", out / "schemas/product.sd")
(out / "hosts.xml").write_text(
    "<hosts>"
    + "".join(
        f'<host name="vespa-products-node{i}.vespa-product-search-net"><alias>node{i}</alias></host>'
        for i in range(n)
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
  <nodes>{content}</nodes>
 </content>
</services>""")
