// Uniqueness constraint (owns a RANGE index), a plain RANGE index, then list them.
CREATE CONSTRAINT person_name IF NOT EXISTS FOR (p:Person) REQUIRE p.name IS UNIQUE;
CREATE INDEX city_name IF NOT EXISTS FOR (c:City) ON (c.name);
SHOW INDEXES YIELD name, type, labelsOrTypes, properties, owningConstraint WHERE type <> "LOOKUP";
