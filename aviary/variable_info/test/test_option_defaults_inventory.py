import subprocess
import sys
import unittest
from pathlib import Path


class OptionDefaultsInventoryTest(unittest.TestCase):
    def test_get_option_defaults_usage_never_increases(self):
        root = Path(__file__).resolve().parents[3]
        result = subprocess.run(
            [sys.executable, root / 'tools/audit_option_defaults.py', '--check'],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
