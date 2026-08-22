#!/usr/bin/env python3
"""Inventory and ratchet uses of get_option_defaults across Aviary.

The report is designed to support the incremental removal requested in #1251.
It records every call site, the local AviaryValues variable receiving the
result, and option keys that are explicitly overwritten later in that scope.
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / 'aviary/variable_info/migrations/option_defaults_inventory.json'
EXCLUDED = {'.git', '.venv', 'venv', 'build', 'dist', '__pycache__'}


def domain_for(path: Path) -> str:
    parts = path.parts
    if len(parts) < 2:
        return 'root'
    if parts[0] != 'aviary':
        return parts[0]
    if parts[1] == 'subsystems' and len(parts) > 2:
        return f'subsystems/{parts[2]}'
    if parts[1] == 'mission' and len(parts) > 2:
        return f'mission/{parts[2]}'
    return parts[1]


def iter_python_files():
    for path in ROOT.rglob('*.py'):
        rel = path.relative_to(ROOT)
        if not any(part in EXCLUDED for part in rel.parts):
            yield rel


def call_is_get_option_defaults(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == 'get_option_defaults'
    if isinstance(func, ast.Attribute):
        return func.attr == 'get_option_defaults'
    return False


def assigned_name(node: ast.Call):
    parent = getattr(node, '_parent', None)
    if not isinstance(parent, (ast.Assign, ast.AnnAssign)):
        return None
    target = parent.target if isinstance(parent, ast.AnnAssign) else parent.targets[0]
    return target.id if isinstance(target, ast.Name) else None


def enclosing_scope(node: ast.AST):
    current = getattr(node, '_parent', None)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            return current
        current = getattr(current, '_parent', None)
    return None


def explicit_sets(scope: ast.AST, receiver: str, after_line: int):
    keys = []
    dynamic = 0
    if not receiver:
        return keys, dynamic
    for node in ast.walk(scope):
        if not isinstance(node, ast.Call) or node.lineno <= after_line:
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == 'set_val'
            and isinstance(func.value, ast.Name)
            and func.value.id == receiver
        ):
            continue
        if not node.args:
            dynamic += 1
            continue
        try:
            keys.append(ast.unparse(node.args[0]))
        except Exception:
            dynamic += 1
    return sorted(set(keys)), dynamic


def analyze_file(rel: Path):
    path = ROOT / rel
    try:
        source = path.read_text(encoding='utf-8')
        tree = ast.parse(source, filename=rel.as_posix())
    except (UnicodeDecodeError, SyntaxError):
        return []

    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child._parent = parent

    records = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not call_is_get_option_defaults(node):
            continue
        receiver = assigned_name(node)
        scope = enclosing_scope(node)
        sets, dynamic = explicit_sets(scope, receiver, node.lineno) if scope else ([], 0)
        records.append(
            {
                'line': node.lineno,
                'receiver': receiver,
                'scope': getattr(scope, 'name', '<module>') if scope else None,
                'explicit_set_keys': sets,
                'dynamic_set_calls': dynamic,
            }
        )
    return records


def build_report():
    files = {}
    domain_counts = Counter()
    total_calls = 0
    total_explicit_keys = 0

    for rel in iter_python_files():
        calls = analyze_file(rel)
        if not calls:
            continue
        domain = domain_for(rel)
        files[rel.as_posix()] = {'domain': domain, 'calls': calls, 'call_count': len(calls)}
        domain_counts[domain] += len(calls)
        total_calls += len(calls)
        total_explicit_keys += sum(len(call['explicit_set_keys']) for call in calls)

    return {
        'issue': 'OpenMDAO/Aviary#1251',
        'purpose': 'Track and eliminate hidden get_option_defaults dependencies without regressions.',
        'summary': {
            'call_count': total_calls,
            'file_count': len(files),
            'explicit_override_key_count': total_explicit_keys,
            'domain_counts': dict(sorted(domain_counts.items())),
        },
        'files': dict(sorted(files.items())),
    }


def compare_to_baseline(current, baseline):
    failures = []
    old_files = baseline.get('files', {})
    new_files = current.get('files', {})

    for path, info in new_files.items():
        current_count = info['call_count']
        baseline_count = old_files.get(path, {}).get('call_count', 0)
        if current_count > baseline_count:
            failures.append(f'{path}: {baseline_count} -> {current_count} calls')

    if current['summary']['call_count'] > baseline['summary']['call_count']:
        failures.append(
            'repository total: '
            f'{baseline["summary"]["call_count"]} -> {current["summary"]["call_count"]} calls'
        )
    return failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--write-manifest', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--manifest', type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()

    report = build_report()
    manifest_path = args.manifest if args.manifest.is_absolute() else ROOT / args.manifest

    if args.write_manifest:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(
            f'Wrote {report["summary"]["call_count"]} calls in '
            f'{report["summary"]["file_count"]} files to {manifest_path.relative_to(ROOT)}'
        )

    if args.check:
        if not manifest_path.exists():
            raise SystemExit(f'manifest not found: {manifest_path}')
        baseline = json.loads(manifest_path.read_text(encoding='utf-8'))
        failures = compare_to_baseline(report, baseline)
        if failures:
            print('get_option_defaults migration ratchet failed:')
            for failure in failures:
                print(f'  {failure}')
            raise SystemExit(1)
        print(
            f'Option-default ratchet passed: {report["summary"]["call_count"]} calls '
            f'across {report["summary"]["file_count"]} files'
        )

    if not args.write_manifest and not args.check:
        print(json.dumps(report['summary'], indent=2))


if __name__ == '__main__':
    main()
