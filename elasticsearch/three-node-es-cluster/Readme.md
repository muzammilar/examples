# Elasticsearch Example - 3 master-eligible nodes, 2 Ingest Nodes, 2 Data Nodes

```sh
docker-compose up --detach --build

# ssh to the app container
docker exec -it chstructingest-1 bash  # if it's running
docker run -it chstructingest bash  # if it's not running

```

Delete *all* unused data after using docker (*Note*: It might have unintended consequences)

```sh
# less safe option
docker system prune --volumes

# safer
docker volume rm three-node-es-cluster_data01
docker volume rm three-node-es-cluster_data02
docker volume rm three-node-es-cluster_data03
```
