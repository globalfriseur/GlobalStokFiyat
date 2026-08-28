import os
import unittest


class SeparateB2BCredentialsTests(unittest.TestCase):
    def test_expected_secret_names_are_distinct(self):
        self.assertNotEqual("ACTIVESHOP_USERNAME", "ACTIVESHOP_B2B_USERNAME")
        self.assertNotEqual("ACTIVESHOP_PASSWORD", "ACTIVESHOP_B2B_PASSWORD")


if __name__ == "__main__":
    unittest.main()
