import sqlite3


class SQLiteClient:
    """The class is used to perform qeuries to SQLite3."""

    def __init__(self, filename: str):
        """Connect to the local sqlite db and return the client object

        :param str filename: json converted to a python struct (using box.Box)
        """
        self.id = "my-unique-id"
        self.conn = sqlite3.connect(filename)
        self.cursor = self.conn.cursor()

    # https://docs.python.org/3/library/stdtypes.html#context-manager-types
    # Enter: Enter the runtime context and return either this object or another object related
    # to the runtime context. The value returned by this method is bound to the identifier
    # in the as clause of with statements using this context manager.
    def __enter__(self):
        """Context management protocol.  Returns self (an instance of AnalyticsDB)."""
        return self

    # Exit: Returning a true value from this method will cause the with statement to suppress
    # the exception and continue execution with the statement immediately following the with statement.
    # Otherwise the exception continues propagating after this method has finished executing.
    def __exit__(self, *args):
        """Context management protocol.  Calls close()"""
        self.close()

    def close(self):
        """Explicitly close database connections before exiting."""
        self.conn.close()

    def create_table(self):
        self.cursor.execute("CREATE TABLE IF NOT EXISTS movie(title, year, score)")

    def insert_data(self):
        self.cursor.execute("""
                INSERT INTO movie VALUES
                    ('Monty Python and the Holy Grail', 1975, 8.2),
                    ('And Now for Something Completely Different', 1971, 7.5)
                """)

    def query_data(self):
        # Parameterized query
        self.cursor.execute("SELECT * FROM movie WHERE score > ?", (1.0,))
        return self.cursor.fetchall()
