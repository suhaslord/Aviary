#!/usr/bin/env python3
"""Trace runtime access to values returned by get_option_defaults().

This migration-only tool monkey-patches get_option_defaults before importing a
test target. Each returned AviaryValues object records which keys are actually
read, checked for membership, overwritten, or exposed through bulk iteration.
The result complements the static inventory in audit_option_defaults.py and is
intended to guide minimal explicit option contracts for issue #1251.
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
import unittest
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aviary.utils.aviary_values import AviaryValues  # noqa: E402
import aviary.variable_info.options as options_module  # noqa: E402


RECORDS = []


def normalize_key(key):
    return key if isinstance(key, str) else repr(key)


def find_callsite():
    for frame in inspect.stack()[2:]:
        path = Path(frame.filename).resolve()
        try:
            rel = path.relative_to(ROOT)
        except ValueError:
            continue
        rel_text = str(rel)
        if rel_text == 'tools/trace_option_defaults.py':
            continue
        if rel_text == 'aviary/variable_info/options.py':
            continue
        return {'file': rel_text, 'line': frame.lineno, 'function': frame.function}
    return {'file': '<unknown>', 'line': 0, 'function': '<unknown>'}


class TrackingAviaryValues(AviaryValues):
    """AviaryValues that records access without changing returned values."""

    def __init__(self, source, callsite):
        super().__init__()
        self._mapping.update(source._mapping)
        self.trace = {
            'callsite': callsite,
            'get_val': set(),
            'get_item': set(),
            'contains': set(),
            'set_val': set(),
            'delete': set(),
            'bulk_access': set(),
        }
        RECORDS.append(self)

    def get_val(self, key, units='unitless'):
        self.trace['get_val'].add(normalize_key(key))
        return super().get_val(key, units)

    def get_item(self, key):
        self.trace['get_item'].add(normalize_key(key))
        return super().get_item(key)

    def set_val(self, key, val, units='unitless', meta_data=None):
        self.trace['set_val'].add(normalize_key(key))
        if meta_data is None:
            return super().set_val(key, val, units)
        return super().set_val(key, val, units, meta_data=meta_data)

    def delete(self, key):
        self.trace['delete'].add(normalize_key(key))
        return super().delete(key)

    def __contains__(self, key):
        self.trace['contains'].add(normalize_key(key))
        return super().__contains__(key)

    def keys(self):
        self.trace['bulk_access'].add('keys')
        return super().keys()

    def items(self):
        self.trace['bulk_access'].add('items')
        return super().items()

    def values(self):
        self.trace['bulk_access'].add('values')
        return super().values()

    def __iter__(self):
        self.trace['bulk_access'].add('iter')
        yield from super().__iter__()


def install_patch():
    original = options_module.get_option_defaults

    def traced_get_option_defaults(*args, **kwargs):
        source = original(*args, **kwargs)
        return TrackingAviaryValues(source, find_callsite())

    options_module.get_option_defaults = traced_get_option_defaults
    return original


def module_from_path(path_text):
    path = Path(path_text)
    if path.suffix == '.py':
        path = path.with_suffix('')
    return '.'.join(path.parts)


def load_suite(args):
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()

    for module in args.module:
        suite.addTests(loader.loadTestsFromName(module_from_path(module)))

    for directory in args.discover:
        start = ROOT / directory
        suite.addTests(
            loader.discover(
                start_dir=str(start),
                pattern=args.pattern,
                top_level_dir=str(ROOT),
            )
        )
    return suite


def serialize_trace(trace):
    result = {'callsite': trace['callsite']}
    for key in ('get_val', 'get_item', 'contains', 'set_val', 'delete', 'bulk_access'):
        result[key] = sorted(trace[key])
    result['value_reads'] = sorted(trace['get_val'] | trace['get_item'])
    return result


def aggregate(records):
    grouped = defaultdict(
        lambda: {
            'instances': 0,
            'get_val': set(),
            'get_item': set(),
            'contains': set(),
            'set_val': set(),
            'delete': set(),
            'bulk_access': set(),
        }
    )
    for record in records:
        trace = record.trace
        site = trace['callsite']
        key = f"{site['file']}:{site['line']}:{site['function']}"
        group = grouped[key]
        group['instances'] += 1
        for field in ('get_val', 'get_item', 'contains', 'set_val', 'delete', 'bulk_access'):
            group[field].update(trace[field])

    output = {}
    for key, group in sorted(grouped.items()):
        output[key] = {
            'instances': group['instances'],
            'value_reads': sorted(group['get_val'] | group['get_item']),
            'membership_checks': sorted(group['contains']),
            'explicit_writes': sorted(group['set_val']),
            'deletes': sorted(group['delete']),
            'bulk_access': sorted(group['bulk_access']),
        }
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--module', action='append', default=[])
    parser.add_argument('--discover', action='append', default=[])
    parser.add_argument('--pattern', default='test*.py')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--verbosity', type=int, default=1)
    args = parser.parse_args()

    if not args.module and not args.discover:
        raise SystemExit('provide at least one --module or --discover target')

    original = install_patch()
    try:
        suite = load_suite(args)
        result = unittest.TextTestRunner(verbosity=args.verbosity).run(suite)
    finally:
        options_module.get_option_defaults = original

    report = {
        'issue': 'OpenMDAO/Aviary#1251',
        'tests': {
            'run': result.testsRun,
            'failures': len(result.failures),
            'errors': len(result.errors),
            'skipped': len(result.skipped),
            'successful': result.wasSuccessful(),
        },
        'get_option_defaults_instances': len(RECORDS),
        'callsite_count': len(aggregate(RECORDS)),
        'callsites': aggregate(RECORDS),
        'instances': [serialize_trace(record.trace) for record in RECORDS],
    }

    rendered = json.dumps(report, indent=2) + '\n'
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding='utf-8')
        print(f'Wrote runtime trace to {output.relative_to(ROOT)}')
    else:
        print(rendered, end='')

    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == '__main__':
    main()
