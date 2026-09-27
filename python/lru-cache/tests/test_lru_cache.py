# tests/test_lru_cache.py

import pytest
import unittest
from unittest.mock import patch
from app.lru_cache import LRUCache


@patch('app.LRUCache.write')
@patch('app.LRUCache.get')
def test_self_update(gt, wrte):

    cache = LRUCache(2)
    cache.write(1,"test1")
    wrte.assert_called_once()
    cache.write(2,"test2")

# Test the add function
def test_no_capacity():
    cache = LRUCache(0)
    # with pytest.raises(ValueError):
    #    cache.write(1,"test")
    cache.write(1,"test")
    assert len(cache.cache) == 0
    assert cache.get(1) is None


class TestLRUCache(unittest.TestCase):
    def test_cache_size_limit(self):
        cache = LRUCache(3)
        cache.write("key1", "value1")
        cache.write("key2", "value2")
        cache.write("key3", "value3")
        self.assertEqual(len(cache.cache), 3)
        self.assertEqual(cache.get_last_item(), ("key3", "value3"))

    def test_lru_eviction(self):
        cache = LRUCache(3)
        cache.write("key1", "value1")
        cache.write("key2", "value2")
        cache.write("key3", "value3")
        cache.write("key4", "value4")
        self.assertEqual(cache.get_last_item(), ("key4", "value4"))
        self.assertIsNone(cache.get("key1"))

    def test_cache_retrieval(self):
        cache = LRUCache(3)
        cache.write("key1", "value1")
        self.assertEqual(cache.get("key1"), "value1")
        self.assertEqual(cache.get_last_item(), ("key1", "value1"))

    def test_cache_update(self):
        cache = LRUCache(3)
        cache.write("key1", "value1")
        cache.write("key1", "new_value")
        self.assertEqual(cache.get_last_item(), ("key1", "new_value"))
        self.assertEqual(cache.get("key1"), "new_value")

    def test_cache_overflow(self):
        cache = LRUCache(3)
        cache.write("key1", "value1")
        cache.write("key2", "value2")
        cache.write("key3", "value3")
        cache.write("key4", "value4")
        cache.write("key5", "value5")
        self.assertEqual(cache.get_last_item(), ("key5", "value5"))
        self.assertIsNone(cache.get("key1"))
        self.assertIsNone(cache.get("key2"))

    def test_cache_get(self):
        cache = LRUCache(3)
        cache.write("key1", "value1")
        cache.write("key2", "value2")
        cache.write("key3", "value3")
        cache.get("key1")
        cache.write("key4", "value4")
        self.assertEqual(cache.get_last_item(), ("key4", "value4"))
        cache.write("key5", "value5")
        self.assertIsNone(cache.get("key3"))
        self.assertIsNone(cache.get("key2"))
        self.assertEqual(cache.get_last_item(), ("key5", "value5"))

    def test_cache_get_non_existent_key(self):
        cache = LRUCache(3)
        self.assertIsNone(cache.get("non_existent_key"))
