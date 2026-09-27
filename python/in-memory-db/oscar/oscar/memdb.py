"""
The goal of this exercise is to build an in memory key value database that supports a set of commands. You will be expected to implement the following commands:

GET key - Prints the value at the specified key, or null.
SET key value - Set the value at the specified key.
UNSET key - Removes the value at the specified key.
EXISTS key - Prints true or false if the key exists in the database.

BEGIN - Start of a transaction.
ROLLBACK - Discard any changes from the start of the last transaction.
COMMIT - Commit any changes from the start of the last transaction to the dataset.

NOTE: Since we're not using a "Transaction ID", we only support one user at a time.

"""

NULL_KEY = "null"

class MemDB:

    """
    An in-memory key-value database supporting basic operations (GET, SET, UNSET, EXISTS)
    and transactional capabilities (BEGIN, ROLLBACK, COMMIT).
    Transactions are nested and uncommitted changes are stored in a stack.
    """

    def __init__(self):
        """
        Initializes the MemDB with an empty main database and an empty list
        to store uncommitted transactions.
        """
        self._db = dict()
        self._uncommitted_transactions = []

    def begin(self):
        """
        Starts a new transaction by pushing an empty dictionary onto the
        uncommitted transactions stack.
        """
        new_transaction = dict()
        self._uncommitted_transactions.append(new_transaction)

    def rollback(self):
        """Discards the latest uncommitted transaction."""

        if self._uncommitted_transactions: # ie self._uncommitted_transactions is not empty list
            self._uncommitted_transactions.pop() # remove the latest transaction

    def commit(self):
        """
        Commits the latest uncommitted transaction to the previous transaction or the main database.
        """
        # ignore results if no transaction exists
        if not self._uncommitted_transactions:
            return

        # remove the last transaction and add the data from the last transaction to the second last transaction
        latest_transaction = self._uncommitted_transactions.pop()

        # use database as the default commit location but update it with second last transaction
        # if it exists
        commit_store = self._db
        if self._uncommitted_transactions: # if there are more than one transactions
            commit_store = self._uncommitted_transactions[-1]

        # take all values from the inner most, nested transaction and write to the commit store
        for key, value in latest_transaction.items():
            commit_store[key] = value

    def get(self, key):
        """
        Retrieves the value associated with the given key.
        It first checks uncommitted transactions (from most recent to oldest)
        and then the main database.
        """
        # iterate over the log first (i.e allow dirty reads/uncommitted reads)
        for trans in reversed(self._uncommitted_transactions):
            if self._exists(trans, key):
                return trans[key]

        # check the actual data base
        return self._db.get(key, NULL_KEY)

    def set (self, key, value):
        """
        Sets the value for the given key. If a transaction is active, the change
        is applied to the current transaction; otherwise, it's applied to the main database.
        """
        # if a transaction exists set this value there
        if self._uncommitted_transactions:
            latest_transaction = self._uncommitted_transactions[-1]
            latest_transaction[key] = value
            return
        # read from the database
        self._db[key] = value

    def unset(self, key):
        """
        Removes the value associated with the given key. If a transaction is active,
        the unset operation is recorded in the current transaction; otherwise, it's applied to the main database.
        """
        # if a transaction exists, the unset the value
        if self._uncommitted_transactions:
            latest_transaction = self._uncommitted_transactions[-1]
            self._unset(latest_transaction, key)
            return
        # if no transaction exists then unset the main database
        self._unset(self._db, key)

    def _unset(self, dct, key):
        """
        Helper method to mark a key as 'unset' (NULL_KEY) within a given dictionary.
        """
        # if we delete we night not know if we actually deleted in transaction
        # if self._exists(dct, key):
        #     del dct[key]
        dct[key] = NULL_KEY

    def exists(self, key) -> bool:
        """
        Checks if a key exists in the database.
        This checks the main database directly, not uncommitted transactions.
        """
        return self._exists(self._db, key)

    def _exists(self, dct, key):
        """
        Helper method to check if a key exists and its value is not NULL_KEY
        within a given dictionary.
        """
        #if key in dct:
        #    return True
        #return False
        return dct.get(key, NULL_KEY) != NULL_KEY

    def print_database(self):
        """Prints the current state of the main database and the transaction log."""
        print("Database: ", self._db)
        print("Transaction Log: ", self._uncommitted_transactions)
