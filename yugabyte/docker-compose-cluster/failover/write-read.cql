-- Write + read while yb-3 is down
INSERT INTO demo.failover (phase, at) VALUES ('yb-3 down', currenttimestamp());
SELECT * FROM demo.failover;
