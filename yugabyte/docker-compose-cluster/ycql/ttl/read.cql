-- TTL, part 2 (6 s later): s1 has expired with the table default, s2 is still there but its
-- secret column expired on its own.
SELECT id, username, secret, TTL(username) FROM demo.sessions;
SELECT count(*) FROM demo.sessions WHERE id = 's1';
