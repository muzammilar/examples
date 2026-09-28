-- Drop database if it exists, so everything is recreated each time
DROP DATABASE IF EXISTS demo;

-- Create the database
CREATE DATABASE demo;

-- Switch to the new database
USE demo;

-- Create a migration history table to track if the migration has been applied
CREATE TABLE IF NOT EXISTS migration_history (
    id INT PRIMARY KEY AUTO_INCREMENT,
    migration_name VARCHAR(255) NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Insert a record indicating this migration was applied (only if it doesn't already exist)
INSERT INTO
    migration_history (migration_name)
SELECT
    'initial_setup'
WHERE
    NOT EXISTS (
        SELECT
            1
        FROM
            migration_history
        WHERE
            migration_name = 'initial_setup'
    );

-- Drop the user if it exists to avoid conflicts, then recreate
DROP USER IF EXISTS 'debezium' @'%';

-- Create user 'debezium' with password 'debezium_pass'
CREATE USER 'debezium' @'%' IDENTIFIED BY 'debezium_pass';

-- Grant necessary permissions to 'debezium' user
GRANT
SELECT
,
    RELOAD,
    SHOW DATABASES,
    REPLICATION SLAVE,
    REPLICATION CLIENT ON *.* TO 'debezium' @'%';

-- Apply the permission changes
FLUSH PRIVILEGES;

-- Drop tables if they exist to recreate each time the script runs
DROP TABLE IF EXISTS products;

DROP TABLE IF EXISTS orders;

DROP TABLE IF EXISTS order_items;

-- Create the 'products' table
CREATE TABLE products (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    price DECIMAL(10, 2) NOT NULL,
    cost_price  DECIMAL(10, 2) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- Create the 'orders' table
CREATE TABLE orders (
    id INT PRIMARY KEY AUTO_INCREMENT,
    customer_name VARCHAR(255) NOT NULL,
    status ENUM('pending', 'completed', 'canceled') DEFAULT 'pending',
    total_amount DECIMAL(10, 2) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- Create the 'order_items' table with a foreign key to 'orders' and 'products'
CREATE TABLE order_items (
    id INT PRIMARY KEY AUTO_INCREMENT,
    order_id INT NOT NULL,
    product_id INT NOT NULL,
    quantity INT NOT NULL DEFAULT 1,
    price DECIMAL(10, 2) NOT NULL,
    cost_price  DECIMAL(10, 2) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE RESTRICT
);

-- Sample data insertion
INSERT INTO
    products (name, description, price, cost_price)
VALUES
    ('Product A', 'Description of product A', 29.99, 20.0),
    ('Product B', 'Description of product B', 19.99, 15.0),
    ('Product C', 'Description of product C', 9.99, 5.0);

INSERT INTO
    orders (customer_name, status, total_amount)
VALUES
    ('John Doe', 'completed', 59.98),
    ('Jane Smith', 'pending', 29.99);

INSERT INTO
    order_items (order_id, product_id, quantity, price, cost_price)
VALUES
    (1, 1, 1, 29.99, 20.0),
    (1, 2, 1, 19.99, 15.0),
    (2, 3, 1, 9.99, 5.0);

-- Ensure all changes are saved
COMMIT;
