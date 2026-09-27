# app/lru_cache.py


"""
LRU Cache
Implementation of LRU Cache using an OrderedDict.
"""

import threading
from collections import OrderedDict


class LRUCache:

    def __init__(self, capacity):
        """
        Constructor for LRUCache.

        :param capacity: The maximum number of items to hold in the cache.
        :type capacity: int
        """
        self.cache = OrderedDict()
        self.capacity = capacity
        self._lock = threading.Lock()


    def write(self, key, value):
        """
        Writes a key-value pair to the cache. If the key already exists, it updates
        the value and moves the key to the most recent position. If adding the key-value
        pair exceeds the cache capacity, it removes the oldest key-value pair.

        :param key: The key to be added or updated in the cache.
        :param value: The value associated with the key.
        """
        with self._lock:
            # update the key cache
            if key in self.cache:
                self.cache.move_to_end(key)  # Move the updated item to the end
            self.cache[key] = value
            if len(self.cache) > self.capacity:
                popped_key, popped_value = self.cache.popitem(last=False)


    def get(self, key):
        """
        Retrieves the value associated with a key from the cache. If the key exists,
        it returns the corresponding value and moves the key to the most recent position.
        If the key does not exist, it returns None.

        :param key: The key to retrieve from the cache.
        :return: The value associated with the key, or None if the key is not present.
        """
        with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)  # Move the accessed key to the end
                return self.cache[key]
        return None

    def get_last_item(self):
        """
        Retrieves the last item (most recently accessed/written) from the cache.

        :return: A tuple containing the key and value of the last item, or None if the cache is empty.
        """
        if not self.cache:
            return None, None
        # The last item in an OrderedDict is the most recently added/moved item
        last_key = next(reversed(self.cache))
        return (last_key, self.cache[last_key])
