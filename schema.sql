CREATE TABLE IF NOT EXISTS students (
 id BIGSERIAL PRIMARY KEY,
 full_name VARCHAR(100) NOT NULL,
 email VARCHAR(254) NOT NULL UNIQUE,
 password_hash TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS password_reset_otps (
 id BIGSERIAL PRIMARY KEY,
 student_id BIGINT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
 otp_hash TEXT NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0,
 last_sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reset_otp_student ON password_reset_otps(student_id);
CREATE TABLE IF NOT EXISTS courses (
 id BIGSERIAL PRIMARY KEY,
 title VARCHAR(160) NOT NULL,
 description TEXT NOT NULL DEFAULT '',
 instructor VARCHAR(100) NOT NULL DEFAULT 'StudyVerse Team'
);
CREATE TABLE IF NOT EXISTS assignments (
 id BIGSERIAL PRIMARY KEY,
 course_id BIGINT REFERENCES courses(id) ON DELETE SET NULL,
 title VARCHAR(160) NOT NULL,
 description TEXT NOT NULL DEFAULT '',
 due_date DATE
);
CREATE TABLE IF NOT EXISTS quiz_results (
 id BIGSERIAL PRIMARY KEY,
 student_id BIGINT REFERENCES students(id) ON DELETE CASCADE,
 quiz_title VARCHAR(160) NOT NULL,
 score NUMERIC(5,2) NOT NULL DEFAULT 0,
 taken_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
INSERT INTO courses(title,description,instructor)
SELECT 'Python Programming','Learn Python fundamentals and OOP.','StudyVerse Team'
WHERE NOT EXISTS(SELECT 1 FROM courses);
INSERT INTO courses(title,description,instructor)
SELECT 'Database Management Systems','Learn SQL and normalization.','StudyVerse Team'
WHERE NOT EXISTS(SELECT 1 FROM courses WHERE title='Database Management Systems');
INSERT INTO courses(title,description,instructor)
SELECT 'Foundations of Operating Systems','Learn processes, scheduling, and memory.','StudyVerse Team'
WHERE NOT EXISTS(SELECT 1 FROM courses WHERE title='Foundations of Operating Systems');
