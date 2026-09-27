"""
ClickHouse Query Class
"""
import typing

import clickhouse_connect

class ClickHouseClient:
    """The class is used to perform qeuries to clickhouse."""

    def __init__(self, config):
        """Connect to the ClickHouse server and return the client object

        :param box.Box config: json converted to a python struct (using box.Box)
        """
        self.id = "my-unique-id"
        self.__db = clickhouse_connect.get_client(host=config.host,
                                                  port=config.port,
                                                  user=config.username,
                                                  password=config.password)

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
        self.__db.close()

    def _query(self, query: str, parameters: typing.Dict[str, typing.Any]):
        return self.__db.query(query=query, parameters=parameters)

    def query(self, query: str, parameters: typing.Dict[str, typing.Any]=None):
        """Returns all rows as a generator/list of tuples with each row being a separate tuple in the list."""
        return self._query(query=query, parameters=parameters).result_rows

    def query_as_named_results(self, query: str, parameters: typing.Dict[str, typing.Any]):
        """Returns all rows as a generator/list of dicts with each row being a separate dict in the list."""
        return self._query(query=query, parameters=parameters).named_results()

    def command(self, query: str):
        return self.__db.command(query)

    def query_as_named_results_and_column_names(self, query: str, parameters: typing.Tuple[
            typing.List, typing.Dict[str, typing.Any]]):
        """Returns all rows as a generator/list of dicts with each row being a separate dict in the list."""
        queryresult = self._query(query=query, parameters=parameters)
        return (queryresult.column_names, queryresult.named_results())

    def query_as_key_value(self, query: str, parameters: typing.Dict[str, typing.Any]):
        """Returns the first two columns of a result as key/value pairs in a single dict."""
        result = {}
        for first, second, *_ in self.query(query=query, parameters=parameters):
            result[first] = second
        return result
