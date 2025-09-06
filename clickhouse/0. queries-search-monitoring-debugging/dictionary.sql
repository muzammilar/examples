
/* List Information about all dictionaries */
SELECT * FROM system.dictionaries;

/* Get all data from a dictionary (one of the ways) */
CREATE DATABASE dictionaries ENGINE = Dictionary;
SELECT * FROM dictionaries.<your dictionary name>;
