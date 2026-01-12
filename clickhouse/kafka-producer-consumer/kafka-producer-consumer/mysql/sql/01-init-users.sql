-- Switch to the test database
USE testdb;

-- Create users table to store user information
-- This table includes basic user details with automatic timestamps
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL,
    email VARCHAR(100) NOT NULL,
    full_name VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- Insert sample user data into the users table
-- These are test users for demonstration purposes
INSERT INTO users (username, email, full_name) VALUES
    ('john_doe', 'john@example.com', 'John Doe'),
    ('jane_smith', 'jane@example.com', 'Jane Smith'),
    ('bob_wilson', 'bob@example.com', 'Bob Wilson'),
    ('alice_jones', 'alice@example.com', 'Alice Jones'),
    ('charlie_brown', 'charlie@example.com', 'Charlie Brown'),
    ('david_miller', 'david@example.com', 'David Miller'),
    ('emma_davis', 'emma@example.com', 'Emma Davis'),
    ('frank_garcia', 'frank@example.com', 'Frank Garcia'),
    ('grace_martinez', 'grace@example.com', 'Grace Martinez'),
    ('henry_rodriguez', 'henry@example.com', 'Henry Rodriguez'),
    ('ivy_lopez', 'ivy@example.com', 'Ivy Lopez'),
    ('jack_lee', 'jack@example.com', 'Jack Lee'),
    ('kate_walker', 'kate@example.com', 'Kate Walker'),
    ('leo_hall', 'leo@example.com', 'Leo Hall'),
    ('mia_allen', 'mia@example.com', 'Mia Allen'),
    ('nathan_young', 'nathan@example.com', 'Nathan Young'),
    ('olivia_king', 'olivia@example.com', 'Olivia King'),
    ('paul_wright', 'paul@example.com', 'Paul Wright'),
    ('quinn_scott', 'quinn@example.com', 'Quinn Scott'),
    ('rachel_green', 'rachel@example.com', 'Rachel Green');
