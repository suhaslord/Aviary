import tempfile
import unittest
from pathlib import Path

from aviary.utils.csv_data_file import read_data_file
from aviary.variable_info.legacy_aliases import resolve_legacy_variable_name
from aviary.variable_info.variable_meta_data import CoreMetaData
from aviary.variable_info.variables import Aircraft


class TestLegacyVariableAliases(unittest.TestCase):
    def test_unknown_name_is_unchanged(self):
        name = 'aircraft:wing:span'
        self.assertEqual(resolve_legacy_variable_name(name), name)

    def test_legacy_csv_header_normalizes_to_canonical_name(self):
        legacy = 'mission:constraints:max_mach'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'legacy.csv'
            path.write_text(f'{legacy}\n0.84\n', encoding='utf-8')
            data, _, _ = read_data_file(path, metadata=CoreMetaData)

        self.assertIn(Aircraft.Design.MAX_MACH, data)
        self.assertNotIn(legacy, data)
        self.assertAlmostEqual(float(data.get_val(Aircraft.Design.MAX_MACH)[0]), 0.84)


if __name__ == '__main__':
    unittest.main()
