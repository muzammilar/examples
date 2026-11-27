"""Bind a native NN/ZKFC role without relying on container IP inference."""

import os
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

role = os.environ.get("HDFS_NAMENODE_ID")
if role:
    if role not in ("nn1", "nn2"):
        raise ValueError("Unknown NameNode role")
    target = Path("/tmp/hadoop-role-conf")
    shutil.copytree("/opt/hadoop/etc/hadoop", target, dirs_exist_ok=True)
    path = target / "hdfs-site.xml"
    tree = ET.parse(path)
    prop = ET.SubElement(tree.getroot(), "property")
    ET.SubElement(prop, "name").text = "dfs.ha.namenode.id"
    ET.SubElement(prop, "value").text = role
    tree.write(path, encoding="unicode")
