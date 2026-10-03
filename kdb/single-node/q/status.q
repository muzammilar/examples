/ status.q: version, threads, memory and tables of the server on localhost:5000
h:hopen `::5000;
show h({`version`release`os`threads`used_bytes`heap_bytes`tables!(.z.K;.z.k;.z.o;system"s";.Q.w[]`used;.Q.w[]`heap;tables[])};::);
exit 0
