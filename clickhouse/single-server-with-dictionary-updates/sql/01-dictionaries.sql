
/* Show all dictionaries */
SELECT * FROM system.dictionaries;

/* Create a database for dictionary updates */
CREATE DATABASE IF NOT EXISTS dictionary ENGINE = Dictionary;

/* Read the data from the dictionary engine */
SHOW TABLES IN dictionary;

;;
