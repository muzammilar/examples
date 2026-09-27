# app/range_array.py

import bisect
import threading


class RangeArray:
    """
    A sorted array implementation that uses the `bisect` module for efficient
    insertion, lookup, deletion, and range queries.

    This class is thread-safe.
    """

    def __init__(self):
        """
        Initializes an empty RangeArray.
        """
        self.data = []
        self._lock = threading.Lock()

    def insert(self, item):
        """
        Inserts an item into the sorted array, maintaining sorted order.
        If the item already exists, it is not inserted again.

        :param item: The item to insert.
        """
        with self._lock:
            if self.get(item) is None: # Check if item already exists to avoid duplicates
                bisect.insort_left(self.data, item)

    def get(self, item):
        """
        Retrieves an item from the array.

        :param item: The item to retrieve.
        :return: The item if found, otherwise None.
        """
        with self._lock:
            index = bisect.bisect_left(self.data, item)
            if index < len(self.data) and self.data[index] == item:
                return self.data[index]
            return None

    def delete(self, item):
        """
        Deletes an item from the array.

        :param item: The item to delete.
        :return: True if the item was deleted, False otherwise.
        """
        with self._lock:
            index = bisect.bisect_left(self.data, item)
            if index < len(self.data) and self.data[index] == item:
                del self.data[index]
                return True
            return False

    def get_range(self, start, end):
        """
        Retrieves all elements within a specified range [start, end] (inclusive).

        :param start: The lower bound of the range.
        :param end: The upper bound of the range.
        :return: A list of elements within the specified range.
        """
        if start is None or end is None:
            raise ValueError("Start and end values cannot be None.")
        if start > end:
            return []  # Return empty list if range is invalid

        with self._lock:
            # Find the index of the first element >= start
            start_index = bisect.bisect_left(self.data, start)
            # Find the index of the first element > end
            end_index = bisect.bisect_right(self.data, end)

            return self.data[start_index:end_index]
