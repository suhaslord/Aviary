import ast
import unittest
from pathlib import Path


class TestOptionDefaultsMigration(unittest.TestCase):
    """Keep the GASP geometry tests moving away from global metadata defaults."""

    def test_no_new_get_option_defaults_imports(self):
        test_dir = Path(__file__).parent

        # These larger test modules are the remaining legacy users in this
        # geometry-test slice. Removing a file from this set is always safe;
        # adding a new dependency on get_option_defaults is not.
        legacy_users = {
            'test_fuselage.py',
            'test_size_group.py',
            'test_wing.py',
        }

        current_users = set()
        for path in test_dir.glob('test_*.py'):
            if path == Path(__file__):
                continue

            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.module != 'aviary.variable_info.options':
                    continue
                if any(alias.name == 'get_option_defaults' for alias in node.names):
                    current_users.add(path.name)
                    break

        unexpected_users = current_users - legacy_users
        self.assertFalse(
            unexpected_users,
            'New GASP geometry tests should declare only the Aviary options they use. '
            f'Unexpected get_option_defaults imports: {sorted(unexpected_users)}',
        )


if __name__ == '__main__':
    unittest.main()
