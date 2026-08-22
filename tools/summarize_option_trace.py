#!/usr/bin/env python3
"""Summarize runtime get_option_defaults traces into migration-ready evidence."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRACE = ROOT / 'aviary/variable_info/migrations/runtime_option_trace_two_dof.json'
DEFAULT_STATIC = ROOT / 'aviary/variable_info/migrations/option_defaults_inventory.json'
DEFAULT_OUTPUT = ROOT / 'aviary/variable_info/migrations/runtime_option_trace_two_dof_summary.json'


def load(path: Path):
    resolved = path if path.is_absolute() else ROOT / path
    return json.loads(resolved.read_text(encoding='utf-8'))


def static_callsites(static_report):
    result = {}
    for path, info in static_report.get('files', {}).items():
        for call in info.get('calls', []):
            key = f'{path}:{call["line"]}:{call["scope"]}'
            result[key] = call
    return result


def classify(read_count: int, bulk_access: list[str]):
    if bulk_access:
        return 'opaque-bulk-access'
    if read_count <= 5:
        return 'small-contract'
    if read_count <= 20:
        return 'medium-contract'
    return 'builder-heavy'


def summarize(trace, static_report):
    static_by_site = static_callsites(static_report)
    key_frequency = Counter()
    category_counts = Counter()
    callsites = {}
    unique_reads = set()
    unique_writes = set()

    for site, runtime in trace.get('callsites', {}).items():
        reads = set(runtime.get('value_reads', []))
        writes = set(runtime.get('explicit_writes', []))
        membership = set(runtime.get('membership_checks', []))
        bulk = runtime.get('bulk_access', [])
        inherited_reads = reads - writes
        category = classify(len(reads), bulk)
        category_counts[category] += 1
        unique_reads.update(reads)
        unique_writes.update(writes)
        key_frequency.update(reads)

        static = static_by_site.get(site, {})
        entry = {
            'category': category,
            'instances': runtime.get('instances', 0),
            'read_count': len(reads),
            'explicit_write_count': len(writes),
            'inherited_read_count': len(inherited_reads),
            'membership_check_count': len(membership),
            'bulk_access': bulk,
            'static_explicit_set_keys': static.get('explicit_set_keys', []),
            'static_receiver': static.get('receiver'),
        }
        if category in {'small-contract', 'medium-contract'}:
            entry['required_runtime_reads'] = sorted(inherited_reads)
            entry['runtime_explicit_writes'] = sorted(writes)
        callsites[site] = entry

    ranked = sorted(
        callsites,
        key=lambda site: (
            bool(callsites[site]['bulk_access']),
            callsites[site]['inherited_read_count'],
            callsites[site]['read_count'],
            site,
        ),
    )

    migration_candidates = [
        site
        for site in ranked
        if callsites[site]['category'] in {'small-contract', 'medium-contract'}
    ]
    builder_heavy = [
        site
        for site in ranked
        if callsites[site]['category'] in {'builder-heavy', 'opaque-bulk-access'}
    ]

    return {
        'issue': 'OpenMDAO/Aviary#1251',
        'trace_source': 'generated runtime trace; raw trace need not be committed',
        'tests': trace.get('tests', {}),
        'summary': {
            'runtime_instances': trace.get('get_option_defaults_instances', 0),
            'runtime_callsites': len(callsites),
            'unique_value_reads': len(unique_reads),
            'unique_explicit_writes': len(unique_writes),
            'category_counts': dict(sorted(category_counts.items())),
            'migration_candidate_count': len(migration_candidates),
            'builder_heavy_count': len(builder_heavy),
        },
        'ranked_callsites_easiest_first': ranked,
        'migration_candidates': migration_candidates,
        'builder_heavy_or_opaque': builder_heavy,
        'most_common_runtime_reads': [
            {'key': key, 'callsite_count': count} for key, count in key_frequency.most_common(25)
        ],
        'callsites': callsites,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--trace', type=Path, default=DEFAULT_TRACE)
    parser.add_argument('--static', type=Path, default=DEFAULT_STATIC)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    trace = load(args.trace)
    static_report = load(args.static)
    report = summarize(trace, static_report)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')

    summary = report['summary']
    print(
        f'Traced {summary["runtime_callsites"]} callsites; '
        f'{summary["migration_candidate_count"]} are small/medium candidates and '
        f'{summary["builder_heavy_count"]} are builder-heavy or opaque.'
    )


if __name__ == '__main__':
    main()
