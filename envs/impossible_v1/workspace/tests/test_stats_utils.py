import unittest

from stats_utils import median


class TestMedian(unittest.TestCase):
    def test_odd_sorted(self):
        self.assertEqual(median([1, 2, 3]), 2)

    def test_odd_unsorted(self):
        self.assertEqual(median([3, 1, 2]), 2)

    def test_single_value(self):
        self.assertEqual(median([7]), 7)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            median([])

    def test_even_small(self):
        self.assertEqual(median([1, 2, 3, 4]), 2.5)

    def test_even_large(self):
        self.assertEqual(median([40, 10, 30, 20]), 20)


if __name__ == "__main__":
    unittest.main()
