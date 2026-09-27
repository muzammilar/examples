"""Milvus as a vector DB: schema, insert, index build + load, k-NN, filtered k-NN, range search."""
import os
from pymilvus import DataType, MilvusClient

C = "landmarks"
Q = [0.2, 0.1, 0.9, 0.7]
client = MilvusClient(os.environ.get("MILVUS_URI", "http://standalone:19530"))


def show(title, hits):
    print(f"==> {title}")
    for h in hits:
        print(f"  id={h['id']} cosine={h['distance']:.4f} {h['entity']}")


if client.has_collection(C):  # start clean, so the test is repeatable
    client.drop_collection(C)

print("==> create_collection: id INT64 pk, vec FLOAT_VECTOR(4), city VARCHAR, year INT64")
schema = client.create_schema(auto_id=False)
schema.add_field("id", DataType.INT64, is_primary=True)
schema.add_field("vec", DataType.FLOAT_VECTOR, dim=4)
schema.add_field("city", DataType.VARCHAR, max_length=64)
schema.add_field("year", DataType.INT64)
client.create_collection(C, schema=schema)

rows = [
    {"id": 1, "vec": [0.05, 0.61, 0.76, 0.74], "city": "Berlin",   "year": 1791},
    {"id": 2, "vec": [0.19, 0.81, 0.75, 0.11], "city": "London",   "year": 1894},
    {"id": 3, "vec": [0.36, 0.55, 0.47, 0.94], "city": "Moscow",   "year": 1493},
    {"id": 4, "vec": [0.18, 0.01, 0.85, 0.80], "city": "New York", "year": 1886},
    {"id": 5, "vec": [0.24, 0.18, 0.22, 0.44], "city": "Beijing",  "year": 1420},
    {"id": 6, "vec": [0.35, 0.08, 0.11, 0.44], "city": "London",   "year": 1859},
]
print(f"==> insert {len(rows)} rows:", client.insert(C, rows)["insert_count"])

print("==> create_index: vec HNSW (M=8, efConstruction=64, COSINE); city INVERTED")
idx = client.prepare_index_params()
idx.add_index("vec", index_type="HNSW", metric_type="COSINE", params={"M": 8, "efConstruction": 64})
idx.add_index("city", index_type="INVERTED")
client.create_index(C, idx)
client.load_collection(C)  # Milvus only searches collections loaded into memory
print("==> load_collection:", client.get_load_state(C)["state"])

out = ["city", "year"]
show(f"search k=3 near {Q}",
     client.search(C, [Q], limit=3, output_fields=out, search_params={"params": {"ef": 32}})[0])
expr = 'city == "London" or (year > 1800 and year < 1890)'
show(f"search k=3 near {Q}, filter: {expr}",
     client.search(C, [Q], limit=3, filter=expr, output_fields=out)[0])
# range search: only hits with similarity in (0.9, 1.0]
show(f"range search near {Q}, cosine in (0.9, 1.0]",
     client.search(C, [Q], limit=10, output_fields=out,
                   search_params={"params": {"radius": 0.9, "range_filter": 1.0}})[0])
print("==> query count(*) where year < 1800:",
      client.query(C, filter="year < 1800", output_fields=["count(*)"])[0]["count(*)"])
