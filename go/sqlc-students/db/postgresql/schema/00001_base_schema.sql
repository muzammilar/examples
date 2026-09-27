CREATE TABLE students (
  id   bigserial PRIMARY KEY,
  name text NOT NULL,
  age  integer NOT NULL
);

CREATE TABLE test_scores (
  student_id bigint NOT NULL,
  score      integer NOT NULL,
  grade      text NOT NULL
);
