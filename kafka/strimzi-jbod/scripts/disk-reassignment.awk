BEGIN {
  printf "{\"version\":1,\"partitions\":["
  separator = ""
}

{
  partition = ""
  replicas = ""
  for (field = 1; field <= NF; field++) {
    if ($field == "Partition:") partition = $(field + 1)
    if ($field == "Replicas:") replicas = $(field + 1)
  }

  if (partition == "" || replicas == "") next

  replica_count = split(replicas, replica_ids, ",")
  replica_list = ""
  directory_list = ""
  for (replica_index = 1; replica_index <= replica_count; replica_index++) {
    if (replica_index > 1) {
      replica_list = replica_list ","
      directory_list = directory_list ","
    }
    replica_list = replica_list replica_ids[replica_index]
    if (replica_ids[replica_index] == broker_id) {
      directory_list = directory_list "\"" target_directory "\""
    } else {
      directory_list = directory_list "\"any\""
    }
  }

  printf "%s{\"topic\":\"%s\",\"partition\":%s,\"replicas\":[%s],\"log_dirs\":[%s]}", separator, topic, partition, replica_list, directory_list
  separator = ","
  found = 1
}

END {
  print "]}"
  if (!found) exit 1
}
