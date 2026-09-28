-- Create the conduktor user if it doesn't exist
DO $ do $ BEGIN IF NOT EXISTS (
    SELECT
    FROM
        pg_catalog.pg_roles
    WHERE
        rolname = 'conduktor'
) THEN CREATE USER conduktor WITH PASSWORD 'conduktor';

END IF;

END $ do $;

-- Create the conduktor database if it doesn't exist
CREATE DATABASE conduktor WITH OWNER = conduktor ENCODING = 'UTF8' CONNECTION
LIMIT
    = -1 TEMPLATE template0 LC_COLLATE = 'en_US.UTF-8' LC_CTYPE = 'en_US.UTF-8';

-- Grant privileges to the conduktor user
GRANT ALL PRIVILEGES ON DATABASE conduktor TO conduktor;