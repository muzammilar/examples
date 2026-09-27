"""
This is the main file for the Oscar in-memory database application.
It demonstrates the usage of the MemDB class with various operations,
including transactions.
"""

from oscar import MemDB


def main():
    """
    Main function to run the database demonstrations.
    It calls check_database for both transaction and non-transaction datasets.
    """

    check_database(dataset_no_transaction())
    check_database(dataset_with_transactions(), debug=True)


def check_database(ops, debug=False):
    """
    Executes a series of database operations on a new MemDB instance.

    Args:
        ops (str): A multiline string where each line represents a database operation.
                   Supported operations are EXISTS, SET, GET, UNSET.
    """

    # create database
    memDb = MemDB()

    # print opertions
    print(ops)

    # execute all operations
    for operation in ops.splitlines():

        # get operation
        op = operation.strip().split(" ")
        command = op[0]

        # execute operation
        if command == "EXISTS":
            print(str(memDb.exists(op[1])).lower())
        elif command == "SET":
            memDb.set(op[1], op[2])
        elif command == "GET":
            print(str(memDb.get(op[1])).lower())
        elif command == "UNSET":
            memDb.unset(op[1])
        elif command == "BEGIN":
            memDb.begin()
        elif command == "ROLLBACK":
            memDb.rollback()
        elif command == "COMMIT":
            memDb.commit()

        # debug
        if debug:
            print("---------OP---------")
            print(operation)
            print("---------DB---------")
            memDb.print_database()
            print("--------------------")

def dataset_no_transaction():
    """
    Returns a string representing a sequence of database operations without transactions.

    Returns:
        str: A multiline string of operations.
    """
    return """
EXISTS A
SET A 123
GET A
EXISTS A
UNSET A
GET A
EXISTS A
"""

def dataset_with_transactions():
    """
    Returns a string representing a sequence of database operations with transactions.

    Returns:
        str: A multiline string of operations.
    """
    return """
SET A 123
GET A
BEGIN
SET A 456
GET A
BEGIN
UNSET A
GET A
COMMIT
EXISTS A
ROLLBACK
GET A
"""

if __name__ == "__main__":
    main()
