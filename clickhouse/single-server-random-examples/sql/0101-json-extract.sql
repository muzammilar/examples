-- Create a table with a JSON field
CREATE TABLE events (
    event_data String
) ENGINE = MergeTree()
ORDER BY event_data;

-- Insert data
INSERT INTO events VALUES (
    '[{"user_id": "user1", "actions": "apple"}, {"user_id": "user2", "actions": "ball"}]'
);

-- Extract the data using JSONExtract and an array of tuple schema
SELECT
      JSONExtract(event_data,'Array(Tuple(user_id String, actions String))')
FROM events;

-- Extract the data using JSONExtract and an array of tuple schema with names
SELECT
      JSONExtract(event_data,'Array(Tuple(user_id String, actions String, nonexisting Nullable(UUID)))')
FROM events;

-- Extract the data using arrayMap and JSONExtractString (for more complex data)
SELECT arrayMap(
      x -> (
          JSONExtractString(x, 'user_id'),
          JSONExtractString(x, 'actions')
      ),
      JSONExtractArrayRaw(event_data)
  )
FROM events;


;;
