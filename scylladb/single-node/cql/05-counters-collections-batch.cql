-- counter columns are incremented in place, never inserted
UPDATE demo.page_views SET views = views + 1 WHERE page = '/home';
UPDATE demo.page_views SET views = views + 5 WHERE page = '/home';
SELECT * FROM demo.page_views;
-- set/map/list columns are updated element-wise without reading the row first
INSERT INTO demo.profiles (username, tags, prefs, logins) VALUES ('bob', {'admin'}, {'theme': 'dark'}, ['2026-01-01']);
UPDATE demo.profiles SET tags = tags + {'beta'}, prefs['lang'] = 'en', logins = logins + ['2026-01-02']
  WHERE username = 'bob';
-- a logged BATCH applies writes to several tables atomically (all or nothing)
BEGIN BATCH
  INSERT INTO demo.users (username, email, balance) VALUES ('carol', 'carol@example.com', 10);
  INSERT INTO demo.profiles (username, tags) VALUES ('carol', {'new'});
APPLY BATCH;
SELECT * FROM demo.profiles;
SELECT * FROM demo.users;
