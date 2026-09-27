
from .chclient import ClickHouseClient


def setup_table(client):
    """
    Create the table if it doesn't exist and populate it with sample data.
    """
    # Create the table
    client.command('''
    CREATE TABLE IF NOT EXISTS sample_table (
        name String,
        score Float32
    ) ENGINE = MergeTree()
    ORDER BY name
    ''')
    print("Table 'sample_table' ensured.")

    # Insert sample data if the table is empty
    if client.query('SELECT COUNT(*) FROM sample_table')[0][0] == 0:
        client.command('''
        INSERT INTO sample_table (name, score) VALUES
        ('Alice', 10.5),
        ('Alice', 10.3),
        ('Alice', 10.1),
        ('Alice', 10.2),
        ('Bob', 10.7),
        ('Bob', 30.7),
        ('Bob', 40.7),
        ('Bob', 20.7),
        ('Bob', 50.7),
        ('Bob', 20.7),
        ('Bob', 30.7),
        ('Bob', 23.7),
        ('Bob', 10.7),
        ('Bob', 24.7),
        ('Charlie', 30.9)
        ''')
        print("Sample data inserted.")


def query_data(client):
    """
    Query data from the table and print the results.
    """
    results = client.query('SELECT sum(score) FROM sample_table WHERE name= {student_name:String}',
                           parameters={'student_name':'Alice'})
    print("Data in 'sample_table':")
    for row in results:
        print(row)
