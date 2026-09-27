-- name: ScoreAndTestsFlattened :many
SELECT students.*, test_scores.*
FROM students
JOIN test_scores ON test_scores.student_id = students.id
WHERE students.id = sqlc.arg(student_id);

-- name: ScoreAndTests :many
SELECT sqlc.embed(students), sqlc.embed(test_scores)
FROM students
JOIN test_scores ON test_scores.student_id = students.id
WHERE students.id = sqlc.arg(student_id);
