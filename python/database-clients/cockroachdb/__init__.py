import sqlalchemy  # cockroachdb


class CockroachClient:
    """The class is used to perform qeuries to clickhouse."""
    # https://docs.sqlalchemy.org/en/20/core/connections.html
    def __init__(self, username: str, password: str, hostname: str):
        """Connect to the Cockroach server and return the client object

        :param str username: username
        :param str password: password
        :param str hostname: hostname
        """
        self.id = "my-unique-id"
        # Define the CockroachDB connection URL
        self.__database_url = f"cockroachdb://{username}:{password}@{hostname}:26257/defaultdb?sslmode=disable"
        #self.engine = sqlalchemy.create_engine(self.__database_url, echo=True)
        self.engine = sqlalchemy.create_engine(self.__database_url)

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
        # self.close()

    def close(self):
        """Explicitly close database connections before exiting."""
        self.engine.close()

    # Create a table
    def create_table(self):
        with self.engine.connect() as conn:
            conn.execute(sqlalchemy.text("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    name STRING NOT NULL,
                    age INT NOT NULL
                )
            """))
            conn.commit()  # commit the transaction

        # verify table exists
        sqlalchemy.inspect(self.engine).has_table("users")

    # Insert data into the table
    def insert_user(self, name, age):
        with self.engine.connect() as conn:
            conn.execute(
                sqlalchemy.text(
                    "INSERT INTO users (name, age) VALUES (:name, :age)"),
                {"name": name, "age": age},
            )
            conn.commit()  # commit the transaction

    # Query data from the table
    def fetch_users(self):
        with self.engine.connect() as conn:
            result = conn.execute(sqlalchemy.text(
                "SELECT id, name, age FROM users"))
            res = []
            for row in result:
                res.append(f"ID: {row.id}, Name: {row.name}, Age: {row.age}")
            return res
