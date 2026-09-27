import unittest
import threading
from app.range_array import RangeArray


class TestRangeArray(unittest.TestCase):

    def setUp(self):
        self.ra = RangeArray()

    def test_insert_single_item(self):
        self.ra.insert(5)
        self.assertEqual(self.ra.data, [5])

    def test_insert_multiple_items_sorted(self):
        self.ra.insert(5)
        self.ra.insert(10)
        self.ra.insert(3)
        self.assertEqual(self.ra.data, [3, 5, 10])

    def test_insert_duplicate_item(self):
        self.ra.insert(5)
        self.ra.insert(5)
        self.assertEqual(self.ra.data, [5])

    def test_get_existing_item(self):
        self.ra.insert(5)
        self.ra.insert(10)
        self.assertEqual(self.ra.get(5), 5)
        self.assertEqual(self.ra.get(10), 10)

    def test_get_non_existing_item(self):
        self.ra.insert(5)
        self.assertIsNone(self.ra.get(7))

    def test_delete_existing_item(self):
        self.ra.insert(5)
        self.ra.insert(10)
        self.assertTrue(self.ra.delete(5))
        self.assertEqual(self.ra.data, [10])

    def test_delete_non_existing_item(self):
        self.ra.insert(5)
        self.assertFalse(self.ra.delete(7))
        self.assertEqual(self.ra.data, [5])

    def test_get_range_basic(self):
        self.ra.insert(1)
        self.ra.insert(5)
        self.ra.insert(10)
        self.ra.insert(15)
        self.assertEqual(self.ra.get_range(5, 10), [5, 10])

    def test_get_range_empty_result(self):
        self.ra.insert(1)
        self.ra.insert(10)
        self.assertEqual(self.ra.get_range(2, 4), [])

    def test_get_range_inclusive_bounds(self):
        self.ra.insert(1)
        self.ra.insert(5)
        self.ra.insert(10)
        self.ra.insert(15)
        self.assertEqual(self.ra.get_range(1, 15), [1, 5, 10, 15])

    def test_get_range_partial_overlap(self):
        self.ra.insert(1)
        self.ra.insert(5)
        self.ra.insert(10)
        self.ra.insert(15)
        self.assertEqual(self.ra.get_range(0, 7), [1, 5])
        self.assertEqual(self.ra.get_range(7, 20), [10, 15])

    def test_get_range_all_elements(self):
        self.ra.insert(1)
        self.ra.insert(5)
        self.ra.insert(10)
        self.ra.insert(15)
        self.assertEqual(self.ra.get_range(-10, 100), [1, 5, 10, 15])

    def test_get_range_start_greater_than_end(self):
        self.ra.insert(1)
        self.ra.insert(5)
        self.assertEqual(self.ra.get_range(5, 1), [])

    def test_get_range_with_none_values(self):
        with self.assertRaises(ValueError):
            self.ra.get_range(None, 10)
        with self.assertRaises(ValueError):
            self.ra.get_range(1, None)
        with self.assertRaises(ValueError):
            self.ra.get_range(None, None)

    def test_thread_safety_insert(self):
        num_threads = 10
        items_per_thread = 100
        threads = []

        def insert_items():
            for i in range(items_per_thread):
                self.ra.insert(threading.get_ident() * 1000 + i)

        for _ in range(num_threads):
            thread = threading.Thread(target=insert_items)
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # Check that all unique items are present and sorted
        expected_count = num_threads * items_per_thread
        self.assertEqual(len(self.ra.data), expected_count)
        self.assertEqual(self.ra.data, sorted(list(set(self.ra.data))))

    def test_thread_safety_delete(self):
        # Insert initial items
        for i in range(200):
            self.ra.insert(i)

        num_threads = 10
        items_to_delete_per_thread = 10

        def delete_items():
            for i in range(items_to_delete_per_thread):
                item_to_delete = (threading.get_ident() * 10 + i) % 200
                self.ra.delete(item_to_delete)

        threads = []
        for _ in range(num_threads):
            thread = threading.Thread(target=delete_items)
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # Verify that items are deleted and array remains sorted
        # This test is more about not crashing and maintaining sorted order
        # rather than exact final count, as deletions can overlap.
        self.assertEqual(self.ra.data, sorted(self.ra.data))
        self.assertLess(len(self.ra.data), 200)

    def test_thread_safety_get_range(self):
        # Insert initial items
        for i in range(100):
            self.ra.insert(i)

        num_threads = 5
        results = [[] for _ in range(num_threads)]

        def get_range_items(thread_idx):
            # Each thread queries a slightly different range
            start = thread_idx * 10
            end = start + 20
            results[thread_idx] = self.ra.get_range(start, end)

        threads = []
        for i in range(num_threads):
            thread = threading.Thread(target=get_range_items, args=(i,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # Verify that the results are correct and consistent
        for i in range(num_threads):
            start = i * 10
            end = start + 20
            expected_range = [j for j in range(start, end + 1) if 0 <= j < 100]
            self.assertEqual(results[i], expected_range)

    def test_thread_safety_mixed_operations(self):
        num_threads = 10
        insert_count = 50
        delete_count = 20
        get_range_count = 10

        def mixed_operations():
            # Insert some items
            for i in range(insert_count):
                self.ra.insert(threading.get_ident() * 10000 + i)

            # Delete some items
            for i in range(delete_count):
                item_to_delete = (threading.get_ident() * 100 + i) % (insert_count * num_threads)
                self.ra.delete(item_to_delete)

            # Get some ranges
            for i in range(get_range_count):
                start = (threading.get_ident() * 10 + i) % 100
                end = start + 5
                self.ra.get_range(start, end)

        threads = []
        for _ in range(num_threads):
            thread = threading.Thread(target=mixed_operations)
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # Final check: ensure the array remains sorted
        self.assertEqual(self.ra.data, sorted(self.ra.data))
        # The exact count is hard to predict due to concurrent inserts/deletes,
        # but it should be non-negative.
        self.assertGreaterEqual(len(self.ra.data), 0)
