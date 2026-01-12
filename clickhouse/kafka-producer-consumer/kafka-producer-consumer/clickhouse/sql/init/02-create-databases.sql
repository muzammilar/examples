-- Create separate databases for Kafka production and consumption
-- This provides better organization and separation of concerns

-- Database for Kafka production tables and materialized views
-- Contains tables that publish data to Kafka topics
CREATE DATABASE IF NOT EXISTS production;

-- Database for Kafka consumption tables and storage
-- Contains tables that consume data from Kafka topics and store it
CREATE DATABASE IF NOT EXISTS consumption;
