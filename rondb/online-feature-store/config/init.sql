-- run once by the image's entrypoint (MYSQL_INITIALIZE_DB=1) before mysqld opens its port
CREATE USER IF NOT EXISTS 'rondb'@'%' IDENTIFIED BY 'rondb';
GRANT ALL PRIVILEGES ON *.* TO 'rondb'@'%';
