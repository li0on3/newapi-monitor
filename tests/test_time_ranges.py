import unittest

from dashboard_time_range import resolve_time_range


class TimeRangeTests(unittest.TestCase):
    def test_custom_range_is_preserved(self):
        result = resolve_time_range(100, 200, now=500, default_seconds=60)
        self.assertEqual((100, 200, False), result)

    def test_all_time_starts_at_zero(self):
        result = resolve_time_range(None, None, now=500, default_seconds=60, all_time=True)
        self.assertEqual((0, 500, True), result)

    def test_default_range_is_used_when_dates_are_missing(self):
        result = resolve_time_range(None, None, now=500, default_seconds=60)
        self.assertEqual((440, 500, False), result)

    def test_invalid_range_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_time_range(300, 200, now=500, default_seconds=60)


if __name__ == "__main__":
    unittest.main()
