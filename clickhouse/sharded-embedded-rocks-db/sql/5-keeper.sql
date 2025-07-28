

/* NEVER USE IN PRODUCTION */
/* Read the Keeper about the state of the replicated database */

SET allow_unrestricted_reads_from_keeper = 'true';

/* Read all the files from zookeeper */
SELECT *
FROM system.zookeeper;

;;
