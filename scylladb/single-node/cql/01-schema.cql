-- fresh RF=1 keyspace (tablets are on by default) with one table per demo
DROP KEYSPACE IF EXISTS demo;
CREATE KEYSPACE demo
  WITH replication = {'class': 'NetworkTopologyStrategy', 'replication_factor': 1};
CREATE TABLE demo.users (username text PRIMARY KEY, email text, balance int);
-- secondary index: lets a query filter on a non-key column
CREATE INDEX users_by_email ON demo.users (email);
-- partition key sensor, clustering key ts: rows in a partition are sorted by ts
CREATE TABLE demo.readings (sensor text, ts timestamp, value double,
  PRIMARY KEY (sensor, ts)) WITH CLUSTERING ORDER BY (ts DESC);
CREATE TABLE demo.page_views (page text PRIMARY KEY, views counter);
CREATE TABLE demo.profiles (username text PRIMARY KEY,
  tags set<text>, prefs map<text, text>, logins list<date>);
