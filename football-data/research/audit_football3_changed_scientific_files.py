from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
from pathlib import Path

SCIENCE_DIR = Path('football-data/research')
CONTRACT_TEMPLATE = SCIENCE_DIR / 'FOOTBALL3_EXPERIMENT_CONTRACT_TEMPLATE_V2.json'
ZERO_LABEL_HDA_PATH = 'football-data/research/football3_hda.py'
HDA_SCORING_PATH = 'football-data/research/football3_hda_scoring.py'
HDA_TEST_PATH = 'football-data/research/test_football3_hda.py'
HDA_AUDIT_PATH = 'football-data/research/run_football3_hda_zero_label_audit.py'
HDA_GUARD_PATH = 'football-data/research/audit_football3_changed_scientific_files.py'
ZERO_LABEL_HDA_MARKER = 'HDA_AGGREGATION_ONLY_NO_TARGET_LABEL_SCORING'
HDA_SCORING_MARKER = 'PURE_HDA_PROBABILITY_SCORING_NO_IO_NO_TRAINING'
HDA_SCORING_MODULE = 'football3_hda_scoring'

# Exact allow-list only. The scoring module is NOT a generic exemption: it has its own
# executable AST purity contract below, and every other scoring caller must bind V2.
EXEMPT_EXACT = {
    'football-data/research/football3_core.py',
    'football-data/research/run_football3_synthetic_prelabel_smoke.py',
    'football-data/research/audit_football3_execution_surface.py',
    'football-data/research/audit_football3_changed_scientific_files.py',
    'football-data/research/audit_football3_lineage.py',
    'football-data/research/audit_football3_pr_scope.py',
    'football-data/research/validate_football3_experiment.py',
    'football-data/research/validate_football3_research_policy_v3.py',
    'football-data/research/test_football3_core.py',
    'football-data/research/test_validate_football3_experiment.py',
    ZERO_LABEL_HDA_PATH,
    HDA_TEST_PATH,
    'football-data/research/run_football3_hda_zero_label_audit.py',
}
DEDICATED_EXEMPT_PRODUCTION_CONTRACTS = {
    'football-data/research/football3_hda.py',
    'football-data/research/audit_football3_changed_scientific_files.py',
    'football-data/research/run_football3_hda_zero_label_audit.py',
}
EXEMPT_TEST_ONLY_PATHS = {'football-data/research/test_football3_hda.py'}

# Dedicated production module identity is not represented by unordered feature sets.
# Full canonical AST structure is checked below; the canonical representation preserves
# field order, tree position, identifiers, constants, call arguments, assignment values,
# function-body ownership and duplicate occurrences while excluding only source-location
# metadata such as lineno/col_offset/end_lineno/end_col_offset.
CANONICAL_AST_SCHEMA = 'football3_canonical_ast_structure_v1'
SCIENTIFIC_CODE_PREFIXES = ('football-data/', 'scripts/')
BLOCKED_EXECUTABLE_SUFFIXES = {'.ipynb', '.sh', '.r', '.R', '.js', '.ts', '.ps1', '.bat', '.cmd'}

ZERO_LABEL_ALLOWED_IMPORTS = {
    ('__future__', ('annotations',)),
    ('hashlib', ()),
    ('json', ()),
    ('math', ()),
    ('dataclasses', ('dataclass',)),
    ('pathlib', ('Path',)),
    ('typing', ('Iterable', 'Mapping', 'Sequence')),
}
ZERO_LABEL_ALLOWED_FUNCTION_SIGNATURES = {
    '_fail': (('code', 'message'), ()),
    '_require_fixed_probability_tolerance': (('value',), ()),
    '_require_fixed_tie_tolerance': (('value',), ()),
    '_validate_class_order': (('class_order',), ()),
    'canonical_required_score_cells': (('required_score_cells',), ()),
    'canonical_support_bytes': (('required_score_cells',), ()),
    'canonical_support_sha256': (('required_score_cells',), ()),
    '_generated_cells': (('generator',), ()),
    'load_score_support_registry': (('path',), ()),
    '_validate_support_contract': ((), ('score_support_id', 'schema_version', 'required_score_cells', 'unresolved_tail')),
    '_coerce_cell': (('raw',), ()),
    'validate_score_matrix': (('score_cells',), ('unresolved_tail', 'tail_probability', 'class_order', 'probability_tolerance', 'score_support_id', 'schema_version', 'required_score_cells')),
    '_validate_hda_probability_mapping': (('probabilities',), ('class_order', 'probability_tolerance')),
    'choose_hda_top1': (('probabilities',), ('class_order', 'probability_tolerance', 'tie_tolerance')),
    '_score_class': (('home_goals', 'away_goals'), ()),
    '_max_specific_score': (('cells', 'aggregated_tail_proxy_totals', 'unresolved_tail', 'tail_probability', 'tie_tolerance'), ()),
    'aggregate_score_matrix_to_hda': (('score_cells',), ('unresolved_tail', 'tail_probability', 'class_order', 'probability_tolerance', 'tie_tolerance', 'score_support_id', 'schema_version', 'required_score_cells')),
}
ZERO_LABEL_ALLOWED_PUBLIC_FUNCTIONS = {
    name for name in ZERO_LABEL_ALLOWED_FUNCTION_SIGNATURES if not name.startswith('_')
}
ZERO_LABEL_ALLOWED_CLASSES = {'HDAValidationError', 'ScoreCell'}
ZERO_LABEL_FORBIDDEN_IDENTIFIERS = {
    'labels', 'target', 'targets', 'truth', 'truths', 'result', 'results',
    'y_true', 'ytrue', 'ground_truth', 'outcome', 'outcomes',
}
ZERO_LABEL_EXISTING_CATEGORY_IDENTIFIER_COUNTS = {'label': 26}

ZERO_LABEL_FORBIDDEN_METRIC_TOKENS = {
    'logloss', 'log_loss', 'brier', 'brier_score_loss', 'rps', 'ranked_probability_score',
    'precision', 'precision_score', 'recall', 'recall_score', 'f1', 'f1_score',
    'confusion', 'confusion_matrix', 'accuracy', 'accuracy_score',
}
ZERO_LABEL_ALLOWED_CALL_FORMS = {
    'name:Path', 'callattr:name:Path.with_name', 'name:dataclass',
    'name:HDAValidationError', 'name:float', 'name:tuple', 'name:set',
    'name:canonical_required_score_cells', 'callattr:attr:json.dumps.encode',
    'callattr:attr:hashlib.sha256.hexdigest', 'attr:generator.get', 'attr:obj.get',
    'name:load_score_support_registry', 'name:canonical_support_sha256', 'name:isinstance',
    'name:ScoreCell', 'name:_require_fixed_probability_tolerance', 'name:_validate_class_order',
    'name:_validate_support_contract', 'name:sorted', 'attr:math.fsum',
    'name:_validate_hda_probability_mapping', 'name:_require_fixed_tie_tolerance', 'name:max',
    'name:_score_class', 'name:validate_score_matrix', 'name:_max_specific_score', 'name:bool',
    'name:super', 'callattr:name:super.__init__', 'name:_fail', 'name:any',
    'attr:seen.add', 'attr:out.append', 'attr:registry_path.is_file', 'attr:json.loads',
    'attr:entry.get', 'attr:names.add', 'name:_generated_cells', 'name:dict', 'name:type',
    'attr:math.isfinite', 'name:_coerce_cell', 'attr:cells.append', 'name:abs', 'name:list',
    'name:len', 'attr:out.values', 'attr:probs.values',
    'subscriptattr:known_values.append', 'attr:base.update', 'name:choose_hda_top1',
    'attr:json.dumps', 'attr:hashlib.sha256', 'attr:registry_path.read_text', 'name:range',
    'name:canonical_support_bytes',
}
ZERO_LABEL_FORBIDDEN_DATA_SUFFIXES = ('.csv', '.parquet', '.feather', '.arrow', '.sqlite', '.db', '.h5', '.hdf5')
ZERO_LABEL_ALLOWED_TOP_LEVEL_CALL_FORMS = {'name:Path', 'callattr:name:Path.with_name'}

ZERO_LABEL_FORBIDDEN_CALLS = {
    'open', 'read_csv', 'read_parquet', 'read_feather', 'read_sql', 'read_sql_query',
    'connect', 'execute', 'executemany', 'urlopen', 'request',
    'popen', 'run', 'call', 'check_call', 'check_output', 'fit', 'fit_transform',
    'predict', 'predict_proba', 'score', 'accuracy_score', 'log_loss', 'precision_score',
    'recall_score', 'f1_score', 'confusion_matrix',
}


SCORING_ALLOWED_IMPORTS = {
    ('__future__', ('annotations',)),
    ('math', ()),
    ('typing', ('Mapping', 'Sequence')),
    ('football3_hda', (
        'DEFAULT_PROBABILITY_TOLERANCE', 'DEFAULT_TIE_TOLERANCE', 'HDA_CLASS_ORDER',
        'HDAValidationError', 'TOP1_TIE', 'choose_hda_top1',
    )),
}
SCORING_ALLOWED_FUNCTION_SIGNATURES = {
    '_fail': (('code', 'message'), ()),
    '_require_contract_constants': (('class_order', 'probability_tolerance', 'tie_tolerance'), ()),
    '_validate_metric_inputs': (('probability_rows', 'labels'), ('class_order', 'probability_tolerance', 'tie_tolerance')),
    'draw_classification_metrics': (('probability_rows', 'labels'), ('class_order', 'probability_tolerance', 'tie_tolerance')),
    'score_hda_probabilities': (('probability_rows', 'labels'), ('class_order', 'probability_tolerance', 'tie_tolerance')),
}
SCORING_ALLOWED_PUBLIC_FUNCTIONS = {'draw_classification_metrics', 'score_hda_probabilities'}
SCORING_FORBIDDEN_IMPORT_PREFIXES = {
    'os', 'sys', 'subprocess', 'socket', 'urllib', 'http', 'requests', 'pandas', 'polars',
    'sqlite3', 'sqlalchemy', 'duckdb', 'psycopg', 'pymysql', 'sklearn', 'torch', 'tensorflow',
}
SCORING_ALLOWED_CALL_FORMS = {
    'name:HDAValidationError', 'name:_require_contract_constants', 'name:enumerate',
    'name:_validate_metric_inputs', 'name:len', 'name:sum', 'name:zip',
    'name:draw_classification_metrics', 'attr:out.update', 'name:_fail', 'name:isinstance',
    'attr:math.fsum', 'attr:rows.append', 'name:choose_hda_top1', 'name:max', 'name:list',
    'name:tuple', 'name:float', 'name:set', 'attr:checked.values', 'name:abs',
    'attr:predicted.append', 'name:int', 'attr:math.isfinite', 'attr:math.isinf',
    'name:str', 'attr:math.log', 'attr:row.values',
}


class GuardError(RuntimeError):
    pass


def git(*args: str) -> str:
    return subprocess.check_output(['git', *args], text=True).strip()


def changed_files(base: str, head: str) -> list[str]:
    out = git('diff', '--name-only', f'{base}...{head}')
    return [x.strip() for x in out.splitlines() if x.strip()]


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding='utf-8'), filename=str(path))


def _string_constant_from_tree(tree: ast.Module, constant_name: str) -> str | None:
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == constant_name for target in targets):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    return None


def _string_constant(path: Path, constant_name: str) -> str | None:
    return _string_constant_from_tree(_parse(path), constant_name)


def contract_constant(path: Path) -> str | None:
    return _string_constant(path, 'FOOTBALL3_EXPERIMENT_CONTRACT')


def helper_contract_constant(path: Path) -> str | None:
    return _string_constant(path, 'FOOTBALL3_EXPERIMENT_HELPER_FOR')


def _import_contract(node: ast.AST) -> list[tuple[str, tuple[str, ...]]]:
    if isinstance(node, ast.Import):
        return [(alias.name, ()) for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        return [(node.module or '', tuple(alias.name for alias in node.names))]
    return []


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if node.args.posonlyargs or node.args.vararg or node.args.kwarg:
        return (('__UNSUPPORTED_SIGNATURE__',), ())
    positional = tuple(arg.arg for arg in node.args.args)
    kwonly = tuple(arg.arg for arg in node.args.kwonlyargs)
    return positional, kwonly


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id.lower()
    if isinstance(func, ast.Attribute):
        return func.attr.lower()
    return None


def _call_form_from_func(func: ast.expr) -> str | None:
    """Return a deliberately narrow structural identity for a call target.

    Dynamic/subscript/lambda/call-returned callables remain unresolved unless an exact
    production form is explicitly represented below.  None is therefore a blocker, not
    an instruction to skip checking the call.
    """
    if isinstance(func, ast.Name):
        return f'name:{func.id}'
    if isinstance(func, ast.Attribute):
        receiver = func.value
        if isinstance(receiver, ast.Name):
            return f'attr:{receiver.id}.{func.attr}'
        if isinstance(receiver, ast.Call):
            inner = _call_form_from_func(receiver.func)
            if inner is not None:
                return f'callattr:{inner}.{func.attr}'
        if isinstance(receiver, ast.Subscript) and isinstance(receiver.value, ast.Name):
            return f'subscriptattr:{receiver.value.id}.{func.attr}'
    return None


def _call_form(node: ast.Call) -> str | None:
    return _call_form_from_func(node.func)


def _dangerous_top_level_calls(tree: ast.Module, forbidden: set[str]) -> list[str]:
    blockers: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                name = _call_name(child)
                if name in forbidden:
                    blockers.append(f'top-level forbidden call: {name}')
    return blockers


def zero_label_hda_blockers(path: Path) -> list[str]:
    blockers: list[str] = []
    try:
        tree = _parse(path)
    except SyntaxError as exc:
        return [f'{path}: syntax error: {exc}']

    marker = _string_constant_from_tree(tree, 'FOOTBALL3_ZERO_LABEL_ENGINEERING_SURFACE')
    if marker != ZERO_LABEL_HDA_MARKER:
        blockers.append(f'{path}: missing zero-label surface marker {ZERO_LABEL_HDA_MARKER}')

    imports: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        imports.extend(_import_contract(node))
    unexpected_imports = sorted(set(imports) - ZERO_LABEL_ALLOWED_IMPORTS)
    if unexpected_imports:
        blockers.append(f'{path}: zero-label module has imports outside AST contract: {unexpected_imports}')

    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if set(functions) != set(ZERO_LABEL_ALLOWED_FUNCTION_SIGNATURES):
        blockers.append(
            f'{path}: zero-label function set mismatch; expected={sorted(ZERO_LABEL_ALLOWED_FUNCTION_SIGNATURES)} actual={sorted(functions)}'
        )
    for name, expected in ZERO_LABEL_ALLOWED_FUNCTION_SIGNATURES.items():
        node = functions.get(name)
        if node is not None and _signature(node) != expected:
            blockers.append(f'{path}: zero-label function signature mismatch for {name}: expected={expected} actual={_signature(node)}')
    public = {name for name in functions if not name.startswith('_')}
    if public != ZERO_LABEL_ALLOWED_PUBLIC_FUNCTIONS:
        blockers.append(f'{path}: zero-label public function set mismatch: {sorted(public)}')

    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    if classes != ZERO_LABEL_ALLOWED_CLASSES:
        blockers.append(f'{path}: zero-label class set mismatch: expected={sorted(ZERO_LABEL_ALLOWED_CLASSES)} actual={sorted(classes)}')

    for node in ast.walk(tree):
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            allowed = False
            if isinstance(node, ast.ClassDef) and node.name == 'ScoreCell' and isinstance(decorator, ast.Call):
                allowed = (
                    _call_form(decorator) == 'name:dataclass'
                    and not decorator.args
                    and len(decorator.keywords) == 1
                    and decorator.keywords[0].arg == 'frozen'
                    and isinstance(decorator.keywords[0].value, ast.Constant)
                    and decorator.keywords[0].value.value is True
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == 'score':
                allowed = isinstance(decorator, ast.Name) and decorator.id == 'property'
            if not allowed:
                blockers.append(f'{path}: decorator outside zero-label AST interface contract on {node.name}: {ast.dump(decorator, include_attributes=False)}')

    identifier_counts = {name: 0 for name in ZERO_LABEL_EXISTING_CATEGORY_IDENTIFIER_COUNTS}
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.arg.lower() in ZERO_LABEL_FORBIDDEN_IDENTIFIERS:
            blockers.append(f'{path}: forbidden target-value parameter identifier: {node.arg}')
        if isinstance(node, ast.arg) and node.arg.lower() in identifier_counts:
            identifier_counts[node.arg.lower()] += 1
        if isinstance(node, ast.Name):
            ident = node.id.lower()
            if ident in identifier_counts:
                identifier_counts[ident] += 1
            if ident in ZERO_LABEL_FORBIDDEN_IDENTIFIERS:
                blockers.append(f'{path}: forbidden target-value identifier: {node.id}')
            if ident in ZERO_LABEL_FORBIDDEN_METRIC_TOKENS:
                blockers.append(f'{path}: forbidden scoring identifier in zero-label module: {node.id}')
        if isinstance(node, ast.Attribute):
            attr = node.attr.lower()
            if attr in ZERO_LABEL_FORBIDDEN_METRIC_TOKENS:
                blockers.append(f'{path}: forbidden scoring attribute in zero-label module: {node.attr}')
        if isinstance(node, ast.Call):
            call = _call_name(node)
            form = _call_form(node)
            if call in ZERO_LABEL_FORBIDDEN_CALLS:
                blockers.append(f'{path}: forbidden I/O/network/model/scoring call in zero-label module: {call}')
            if form is None:
                blockers.append(f'{path}: unresolved ast.Call target in zero-label module: {ast.dump(node.func, include_attributes=False)}')
            elif form not in ZERO_LABEL_ALLOWED_CALL_FORMS:
                blockers.append(f'{path}: call outside zero-label AST interface contract: {form}')
        if isinstance(node, ast.Lambda):
            blockers.append(f'{path}: lambda callable is outside zero-label AST interface contract')
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.lower()
            if lowered.endswith(ZERO_LABEL_FORBIDDEN_DATA_SUFFIXES):
                blockers.append(f'{path}: forbidden formal/result data path literal in zero-label module: {node.value}')

    for ident, expected_count in ZERO_LABEL_EXISTING_CATEGORY_IDENTIFIER_COUNTS.items():
        if identifier_counts[ident] != expected_count:
            blockers.append(f'{path}: zero-label category identifier count mismatch for {ident}: expected={expected_count} actual={identifier_counts[ident]}')

    for function in functions.values():
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and _call_name(node) == 'read_text' and function.name != 'load_score_support_registry':
                blockers.append(f'{path}: file reading is allowed only inside load_score_support_registry')

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                form = _call_form(child)
                if form is None:
                    blockers.append(f'{path}: unresolved top-level ast.Call target in zero-label module: {ast.dump(child.func, include_attributes=False)}')
                elif form not in ZERO_LABEL_ALLOWED_TOP_LEVEL_CALL_FORMS:
                    blockers.append(f'{path}: top-level business-side-effect call outside contract: {form}')

    for detail in _dangerous_top_level_calls(tree, ZERO_LABEL_FORBIDDEN_CALLS):
        blockers.append(f'{path}: {detail}')
    return sorted(set(blockers))


def scoring_module_blockers(path: Path) -> list[str]:
    blockers: list[str] = []
    try:
        tree = _parse(path)
    except SyntaxError as exc:
        return [f'{path}: syntax error: {exc}']

    marker = _string_constant_from_tree(tree, 'FOOTBALL3_HDA_SCORING_INFRASTRUCTURE')
    if marker != HDA_SCORING_MARKER:
        blockers.append(f'{path}: missing pure-scoring marker {HDA_SCORING_MARKER}')

    imports: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        imports.extend(_import_contract(node))
    unexpected = sorted(set(imports) - SCORING_ALLOWED_IMPORTS)
    if unexpected:
        blockers.append(f'{path}: scoring module has imports outside purity contract: {unexpected}')
    for module, _ in imports:
        if any(module == prefix or module.startswith(prefix + '.') for prefix in SCORING_FORBIDDEN_IMPORT_PREFIXES):
            blockers.append(f'{path}: forbidden scoring infrastructure import: {module}')

    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if set(functions) != set(SCORING_ALLOWED_FUNCTION_SIGNATURES):
        blockers.append(f'{path}: scoring function set mismatch: expected={sorted(SCORING_ALLOWED_FUNCTION_SIGNATURES)} actual={sorted(functions)}')
    for name, expected in SCORING_ALLOWED_FUNCTION_SIGNATURES.items():
        node = functions.get(name)
        if node is not None and _signature(node) != expected:
            blockers.append(f'{path}: scoring function signature mismatch for {name}: expected={expected} actual={_signature(node)}')
    public = {name for name in functions if not name.startswith('_')}
    if public != SCORING_ALLOWED_PUBLIC_FUNCTIONS:
        blockers.append(f'{path}: scoring public function set mismatch: {sorted(public)}')

    classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    if classes:
        blockers.append(f'{path}: scoring module class definitions are outside purity contract: {sorted(classes)}')

    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list:
            blockers.append(f'{path}: scoring module decorators are outside purity contract on {node.name}')
        if isinstance(node, ast.Lambda):
            blockers.append(f'{path}: scoring module lambda callable is outside purity contract')
        if isinstance(node, ast.Call):
            form = _call_form(node)
            if form is None:
                blockers.append(f'{path}: unresolved ast.Call target in scoring module: {ast.dump(node.func, include_attributes=False)}')
            elif form not in SCORING_ALLOWED_CALL_FORMS:
                blockers.append(f'{path}: scoring call outside explicit purity contract: {form}')
    return sorted(set(blockers))


def _resolve_static_string(node: ast.AST, bindings: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _resolve_static_string(node.left, bindings)
        right = _resolve_static_string(node.right, bindings)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                resolved = _resolve_static_string(value.value, bindings)
                if resolved is None:
                    return None
                parts.append(resolved)
            else:
                return None
        return ''.join(parts)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'join':
        separator = _resolve_static_string(node.func.value, bindings)
        if separator is None or len(node.args) != 1 or node.keywords:
            return None
        seq = node.args[0]
        if not isinstance(seq, (ast.List, ast.Tuple)):
            return None
        items = [_resolve_static_string(item, bindings) for item in seq.elts]
        if any(item is None for item in items):
            return None
        return separator.join(item for item in items if item is not None)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'format':
        template = _resolve_static_string(node.func.value, bindings)
        if template is None or node.keywords:
            return None
        args = [_resolve_static_string(arg, bindings) for arg in node.args]
        if any(arg is None for arg in args):
            return None
        try:
            return template.format(*(arg for arg in args if arg is not None))
        except (IndexError, KeyError, ValueError):
            return None
    return None


def _static_string_bindings(tree: ast.Module) -> dict[str, str]:
    assignments: list[tuple[str, ast.AST]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                assignments.append((target.id, node.value))
    bindings: dict[str, str] = {}
    for _ in range(len(assignments) + 1):
        changed = False
        for name, value in assignments:
            resolved = _resolve_static_string(value, bindings)
            if resolved is not None and bindings.get(name) != resolved:
                bindings[name] = resolved
                changed = True
        if not changed:
            break
    return bindings



def _canonical_ast_value(value: object) -> object:
    if isinstance(value, ast.AST):
        return [
            'node',
            type(value).__name__,
            [[field, _canonical_ast_value(field_value)] for field, field_value in ast.iter_fields(value)],
        ]
    if isinstance(value, list):
        return ['list', [_canonical_ast_value(item) for item in value]]
    if isinstance(value, tuple):
        return ['tuple', [_canonical_ast_value(item) for item in value]]
    return ['literal', type(value).__name__, repr(value)]


def canonical_ast_structure(tree: ast.AST) -> str:
    """Ordered semantic AST identity; location metadata is intentionally absent."""
    payload = {
        'schema': CANONICAL_AST_SCHEMA,
        'tree': _canonical_ast_value(tree),
    }
    return json.dumps(payload, separators=(',', ':'), ensure_ascii=True)


def canonical_ast_sha256(path: Path) -> str:
    tree = _parse(path)
    return hashlib.sha256(canonical_ast_structure(tree).encode('utf-8')).hexdigest()


def _literal_top_level_assignment(tree: ast.Module, name: str) -> tuple[bool, object | None]:
    matches: list[ast.AST] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name:
                matches.append(node.value)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name and node.value is not None:
                matches.append(node.value)
    if len(matches) != 1:
        return False, None
    try:
        return True, ast.literal_eval(matches[0])
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False, None


def _guard_frozen_semantic_blockers(tree: ast.Module, path: Path) -> list[str]:
    blockers: list[str] = []
    expectations = {
        'EXEMPT_TEST_ONLY_PATHS': {'football-data/research/test_football3_hda.py'},
        'DEDICATED_EXEMPT_PRODUCTION_CONTRACTS': {
            'football-data/research/football3_hda.py',
            'football-data/research/audit_football3_changed_scientific_files.py',
            'football-data/research/run_football3_hda_zero_label_audit.py',
        },
        'DYNAMIC_BUILTIN_CAPABILITIES': {'__import__', 'eval', 'exec', 'compile', 'getattr'},
    }
    for name, expected in expectations.items():
        ok, actual = _literal_top_level_assignment(tree, name)
        if not ok or actual != expected:
            blockers.append(
                f'{path}: FROZEN_SEMANTIC_ASSERTION_FAILED {name}; expected={sorted(expected)} actual={actual!r}'
            )
    return blockers


def _audit_frozen_semantic_blockers(tree: ast.Module, path: Path) -> list[str]:
    blockers: list[str] = []
    expectations = {
        'EXPECTED_TEST_COUNT': 93,
        'EXPECTED_FAIL_CLOSED_COUNT': 62,
        'STATUS': 'GPT_REMEDIATED_R5_PENDING_CODEX_RECHECK',
    }
    for name, expected in expectations.items():
        ok, actual = _literal_top_level_assignment(tree, name)
        if not ok or actual != expected:
            blockers.append(
                f'{path}: FROZEN_SEMANTIC_ASSERTION_FAILED {name}; expected={expected!r} actual={actual!r}'
            )
    return blockers


def _strict_module_ast_contract_blockers(
    path: Path,
    *,
    expected_canonical_ast_sha256: str,
    contract_name: str,
    semantic_checker: object,
) -> list[str]:
    try:
        tree = _parse(path)
    except SyntaxError as exc:
        return [f'{path}: {contract_name} syntax error: {exc}']

    actual_sha256 = hashlib.sha256(canonical_ast_structure(tree).encode('utf-8')).hexdigest()
    blockers: list[str] = []
    if actual_sha256 != expected_canonical_ast_sha256:
        blockers.append(
            f'{path}: {contract_name} CANONICAL_AST_SHA256_MISMATCH; '
            f'expected={expected_canonical_ast_sha256} actual={actual_sha256}'
        )
    if semantic_checker is _guard_frozen_semantic_blockers:
        blockers.extend(_guard_frozen_semantic_blockers(tree, path))
    elif semantic_checker is _audit_frozen_semantic_blockers:
        blockers.extend(_audit_frozen_semantic_blockers(tree, path))
    else:
        blockers.append(f'{path}: {contract_name} unknown semantic checker')
    return blockers


def _require_external_canonical_ast_sha256(raw: str, *, label: str) -> str:
    if (
        not isinstance(raw, str)
        or len(raw) != 64
        or any(ch not in '0123456789abcdef' for ch in raw)
    ):
        raise GuardError(f'{label} must be exactly 64 lowercase hexadecimal characters')
    return raw


def guard_module_blockers(
    path: Path,
    *,
    expected_canonical_ast_sha256: str,
) -> list[str]:
    expected = _require_external_canonical_ast_sha256(
        expected_canonical_ast_sha256,
        label='expected guard canonical AST SHA-256',
    )
    return _strict_module_ast_contract_blockers(
        path,
        expected_canonical_ast_sha256=expected,
        contract_name='FOOTBALL3_GUARD_DEDICATED_AST_CONTRACT_CANONICAL_V1',
        semantic_checker=_guard_frozen_semantic_blockers,
    )


def hda_audit_module_blockers(
    path: Path,
    *,
    expected_canonical_ast_sha256: str,
) -> list[str]:
    expected = _require_external_canonical_ast_sha256(
        expected_canonical_ast_sha256,
        label='expected audit canonical AST SHA-256',
    )
    return _strict_module_ast_contract_blockers(
        path,
        expected_canonical_ast_sha256=expected,
        contract_name='FOOTBALL3_HDA_AUDIT_DEDICATED_AST_CONTRACT_CANONICAL_V1',
        semantic_checker=_audit_frozen_semantic_blockers,
    )


AUTH_BUILTINS_MODULE = 'BUILTINS_MODULE'
AUTH_IMPORTLIB_MODULE = 'IMPORTLIB_MODULE'
AUTH_DYNAMIC_CAPABILITY = 'DYNAMIC_CAPABILITY'
AUTH_DERIVED = 'AUTHORITY_DERIVED'
AUTH_CONTAINER = 'AUTHORITY_CONTAINER'
DYNAMIC_BUILTIN_CAPABILITIES = {'__import__', 'eval', 'exec', 'compile', 'getattr'}
AUTHORITY_KINDS = {
    AUTH_BUILTINS_MODULE,
    AUTH_IMPORTLIB_MODULE,
    AUTH_DYNAMIC_CAPABILITY,
    AUTH_DERIVED,
    AUTH_CONTAINER,
}


def _assignment_target_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        out: list[str] = []
        for item in target.elts:
            out.extend(_assignment_target_names(item))
        return out
    if isinstance(target, ast.Starred):
        return _assignment_target_names(target.value)
    return []


def _merge_authority_kinds(kinds: list[str | None], *, container: bool = False) -> str | None:
    live = [kind for kind in kinds if kind in AUTHORITY_KINDS]
    if not live:
        return None
    if container:
        return AUTH_CONTAINER
    if all(kind == live[0] for kind in live) and live[0] in {AUTH_BUILTINS_MODULE, AUTH_IMPORTLIB_MODULE}:
        return live[0]
    return AUTH_DERIVED


def _function_return_kinds(tree: ast.Module, kinds: dict[str, str], function_returns: dict[str, str]) -> dict[str, str]:
    out = dict(function_returns)
    for fn in (node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
        local_kinds = dict(kinds)

        positional = list(fn.args.posonlyargs) + list(fn.args.args)
        if fn.args.defaults:
            for arg, default in zip(positional[-len(fn.args.defaults):], fn.args.defaults):
                default_kind = _authority_expr_kind(default, kinds, out)
                if default_kind in AUTHORITY_KINDS:
                    local_kinds[arg.arg] = default_kind
        for arg, default in zip(fn.args.kwonlyargs, fn.args.kw_defaults):
            if default is None:
                continue
            default_kind = _authority_expr_kind(default, kinds, out)
            if default_kind in AUTHORITY_KINDS:
                local_kinds[arg.arg] = default_kind

        local_assignments: list[tuple[list[str], ast.AST]] = []
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    names = _assignment_target_names(target)
                    if names:
                        local_assignments.append((names, node.value))
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                names = _assignment_target_names(node.target)
                if names:
                    local_assignments.append((names, node.value))
            elif isinstance(node, ast.NamedExpr):
                names = _assignment_target_names(node.target)
                if names:
                    local_assignments.append((names, node.value))
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                names = _assignment_target_names(node.target)
                if names:
                    local_assignments.append((names, node.iter))

        for _ in range(len(local_assignments) + 2):
            changed = False
            for names, value in local_assignments:
                kind = _authority_expr_kind(value, local_kinds, out)
                if kind is None:
                    continue
                for name in names:
                    if local_kinds.get(name) != kind:
                        local_kinds[name] = kind
                        changed = True
            if not changed:
                break

        returned: list[str | None] = []
        for node in ast.walk(fn):
            if isinstance(node, ast.Return) and node.value is not None:
                returned.append(_authority_expr_kind(node.value, local_kinds, out))
        merged = _merge_authority_kinds(returned)
        if merged is not None:
            out[fn.name] = merged
    return out


def _authority_expr_kind(
    node: ast.AST,
    kinds: dict[str, str],
    function_returns: dict[str, str] | None = None,
) -> str | None:
    function_returns = function_returns or {}
    if isinstance(node, ast.Name):
        if node.id in kinds:
            return kinds[node.id]
        if node.id in DYNAMIC_BUILTIN_CAPABILITIES:
            return AUTH_DYNAMIC_CAPABILITY
        if node.id == '__builtins__':
            return AUTH_BUILTINS_MODULE
        return None
    if isinstance(node, ast.Starred):
        return _authority_expr_kind(node.value, kinds, function_returns)
    if isinstance(node, ast.Attribute):
        base = _authority_expr_kind(node.value, kinds, function_returns)
        if base == AUTH_IMPORTLIB_MODULE:
            return AUTH_DYNAMIC_CAPABILITY if node.attr == 'import_module' else AUTH_DERIVED
        if base == AUTH_BUILTINS_MODULE:
            return AUTH_DYNAMIC_CAPABILITY
        if base in {AUTH_DYNAMIC_CAPABILITY, AUTH_DERIVED, AUTH_CONTAINER}:
            return AUTH_DERIVED
        return None
    if isinstance(node, ast.Subscript):
        base = _authority_expr_kind(node.value, kinds, function_returns)
        if base in AUTHORITY_KINDS:
            return AUTH_DYNAMIC_CAPABILITY
        return None
    if isinstance(node, ast.IfExp):
        return _merge_authority_kinds([
            _authority_expr_kind(node.body, kinds, function_returns),
            _authority_expr_kind(node.orelse, kinds, function_returns),
        ])
    if isinstance(node, ast.BoolOp):
        return _merge_authority_kinds([
            _authority_expr_kind(value, kinds, function_returns) for value in node.values
        ])
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return _merge_authority_kinds([
            _authority_expr_kind(item, kinds, function_returns) for item in node.elts
        ], container=True)
    if isinstance(node, ast.Dict):
        values = [item for item in node.values if item is not None]
        keys = [item for item in node.keys if item is not None]
        return _merge_authority_kinds([
            _authority_expr_kind(item, kinds, function_returns) for item in values + keys
        ], container=True)
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        parts: list[ast.AST] = [node.elt]
        for generator in node.generators:
            parts.append(generator.iter)
            parts.extend(generator.ifs)
        return _merge_authority_kinds([
            _authority_expr_kind(item, kinds, function_returns) for item in parts
        ], container=True)
    if isinstance(node, ast.DictComp):
        parts = [node.key, node.value]
        for generator in node.generators:
            parts.append(generator.iter)
            parts.extend(generator.ifs)
        return _merge_authority_kinds([
            _authority_expr_kind(item, kinds, function_returns) for item in parts
        ], container=True)
    if isinstance(node, (ast.BinOp, ast.UnaryOp)):
        children = [child for child in ast.iter_child_nodes(node) if isinstance(child, ast.expr)]
        return _merge_authority_kinds([
            _authority_expr_kind(child, kinds, function_returns) for child in children
        ])
    if isinstance(node, ast.Call):
        func_kind = _authority_expr_kind(node.func, kinds, function_returns)
        if func_kind in AUTHORITY_KINDS:
            return AUTH_DERIVED
        if isinstance(node.func, ast.Name) and node.func.id in function_returns:
            return function_returns[node.func.id]
        arg_kinds = [
            _authority_expr_kind(arg, kinds, function_returns) for arg in node.args
        ]
        arg_kinds.extend(
            _authority_expr_kind(keyword.value, kinds, function_returns)
            for keyword in node.keywords
        )
        if any(kind in AUTHORITY_KINDS for kind in arg_kinds):
            return AUTH_DERIVED
        return None
    return None


def _authority_kinds(tree: ast.Module) -> tuple[dict[str, str], dict[str, str]]:
    kinds: dict[str, str] = {'__builtins__': AUTH_BUILTINS_MODULE}
    for name in DYNAMIC_BUILTIN_CAPABILITIES:
        kinds[name] = AUTH_DYNAMIC_CAPABILITY
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split('.')[0]
                if alias.name == 'builtins':
                    kinds[bound] = AUTH_BUILTINS_MODULE
                elif alias.name == 'importlib':
                    kinds[bound] = AUTH_IMPORTLIB_MODULE
        elif isinstance(node, ast.ImportFrom):
            if node.module == 'builtins':
                for alias in node.names:
                    bound = alias.asname or alias.name
                    if alias.name in DYNAMIC_BUILTIN_CAPABILITIES:
                        kinds[bound] = AUTH_DYNAMIC_CAPABILITY
            elif node.module == 'importlib':
                for alias in node.names:
                    bound = alias.asname or alias.name
                    if alias.name == 'import_module':
                        kinds[bound] = AUTH_DYNAMIC_CAPABILITY

    assignments: list[tuple[list[str], ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                names = _assignment_target_names(target)
                if names:
                    assignments.append((names, node.value))
        elif isinstance(node, ast.AnnAssign):
            names = _assignment_target_names(node.target)
            if names and node.value is not None:
                assignments.append((names, node.value))
        elif isinstance(node, ast.NamedExpr):
            names = _assignment_target_names(node.target)
            if names:
                assignments.append((names, node.value))
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            names = _assignment_target_names(node.target)
            if names:
                assignments.append((names, node.iter))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    names = _assignment_target_names(item.optional_vars)
                    if names:
                        assignments.append((names, item.context_expr))

    function_returns: dict[str, str] = {}
    max_rounds = len(assignments) + sum(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in ast.walk(tree)
    ) + 2
    for _ in range(max_rounds):
        changed = False
        new_function_returns = _function_return_kinds(tree, kinds, function_returns)
        if new_function_returns != function_returns:
            function_returns = new_function_returns
            changed = True
        for names, value in assignments:
            kind = _authority_expr_kind(value, kinds, function_returns)
            if kind is None:
                continue
            for name in names:
                if kinds.get(name) != kind:
                    kinds[name] = kind
                    changed = True
        if not changed:
            break
    return kinds, function_returns


def dynamic_authority_blockers(path: Path) -> list[str]:
    """Reject dynamic execution/import/reflection authority by capability and data-flow closure."""
    try:
        tree = _parse(path)
    except SyntaxError as exc:
        return [f'syntax error blocks authority analysis: {exc}']
    kinds, function_returns = _authority_kinds(tree)
    blockers: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {'builtins', 'importlib'}:
            for alias in node.names:
                bound = alias.asname or alias.name
                if kinds.get(bound) == AUTH_DYNAMIC_CAPABILITY:
                    blockers.append(
                        f'dynamic execution/import/reflection authority imported at line {node.lineno}: {node.module}.{alias.name}'
                    )
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            value = node.value
            kind = _authority_expr_kind(value, kinds, function_returns) if value is not None else None
            if kind in {AUTH_DYNAMIC_CAPABILITY, AUTH_DERIVED, AUTH_CONTAINER}:
                blockers.append(
                    f'dynamic execution/import/reflection authority propagated at line {node.lineno}: {ast.dump(value, include_attributes=False)}'
                )
        if isinstance(node, (ast.For, ast.AsyncFor)):
            kind = _authority_expr_kind(node.iter, kinds, function_returns)
            if kind in AUTHORITY_KINDS:
                blockers.append(
                    f'dynamic execution/import/reflection authority iteration denied at line {node.lineno}: {ast.dump(node.iter, include_attributes=False)}'
                )
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                kind = _authority_expr_kind(item.context_expr, kinds, function_returns)
                if kind in AUTHORITY_KINDS:
                    blockers.append(
                        f'dynamic execution/import/reflection authority context propagation denied at line {node.lineno}: {ast.dump(item.context_expr, include_attributes=False)}'
                    )
        if isinstance(node, ast.Return) and node.value is not None:
            kind = _authority_expr_kind(node.value, kinds, function_returns)
            if kind in AUTHORITY_KINDS:
                blockers.append(
                    f'dynamic execution/import/reflection authority return denied at line {node.lineno}: {ast.dump(node.value, include_attributes=False)}'
                )
        if isinstance(node, ast.Call):
            func_kind = _authority_expr_kind(node.func, kinds, function_returns)
            call_kind = _authority_expr_kind(node, kinds, function_returns)
            arg_kinds = [
                _authority_expr_kind(arg, kinds, function_returns) for arg in node.args
            ]
            arg_kinds.extend(
                _authority_expr_kind(keyword.value, kinds, function_returns)
                for keyword in node.keywords
            )
            if func_kind in AUTHORITY_KINDS:
                blockers.append(
                    f'dynamic execution/import/reflection authority call denied at line {node.lineno}: {ast.dump(node.func, include_attributes=False)}'
                )
            elif any(kind in AUTHORITY_KINDS for kind in arg_kinds):
                blockers.append(
                    f'dynamic execution/import/reflection authority argument propagation denied at line {node.lineno}: {ast.dump(node, include_attributes=False)}'
                )
            elif call_kind in AUTHORITY_KINDS:
                blockers.append(
                    f'dynamic execution/import/reflection authority derivation denied at line {node.lineno}: {ast.dump(node, include_attributes=False)}'
                )
        if isinstance(node, (ast.Attribute, ast.Subscript)):
            kind = _authority_expr_kind(node, kinds, function_returns)
            if kind in {AUTH_DYNAMIC_CAPABILITY, AUTH_DERIVED}:
                blockers.append(
                    f'dynamic execution/import/reflection authority access denied at line {node.lineno}: {ast.dump(node, include_attributes=False)}'
                )
    return sorted(set(blockers))


def references_hda_scoring(path: Path) -> bool:
    """Detect direct HDA-scoring references; dynamic authority is checked separately."""
    try:
        tree = _parse(path)
    except SyntaxError:
        return False
    bindings = _static_string_bindings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == HDA_SCORING_MODULE for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module == HDA_SCORING_MODULE:
            return True
        elif isinstance(node, ast.Call):
            for arg in node.args:
                if _resolve_static_string(arg, bindings) == HDA_SCORING_MODULE:
                    return True
    return False

def is_infra_python(path: str) -> bool:
    return path in EXEMPT_EXACT


def _safe_contract_path(raw: str) -> Path:
    p = Path(raw)
    if p.is_absolute() or '..' in p.parts or not p.as_posix().startswith('football-data/research/'):
        raise GuardError(f'contract path must be repo-relative under football-data/research: {raw}')
    return p


def active_v2_contracts() -> list[Path]:
    out = []
    for p in SCIENCE_DIR.rglob('*.json'):
        if p == CONTRACT_TEMPLATE:
            continue
        try:
            obj = json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get('schema_version') == 2 and obj.get('project_id') == 'football3':
            out.append(p)
    return sorted(out)


def all_contract_runners() -> dict[Path, list[Path]]:
    mapping: dict[Path, list[Path]] = {}
    for p in SCIENCE_DIR.rglob('*.py'):
        try:
            cp = contract_constant(p)
        except SyntaxError:
            continue
        if not cp:
            continue
        try:
            cpath = _safe_contract_path(cp)
        except GuardError:
            continue
        mapping.setdefault(cpath, []).append(p)
    return mapping


def run_preflight(runner: Path, contract: Path) -> tuple[int, str]:
    result = subprocess.run([
        'python', 'football-data/research/validate_football3_experiment.py',
        '--contract', contract.as_posix(), '--runner', runner.as_posix(),
    ], text=True, capture_output=True)
    message = (result.stdout + '\n' + result.stderr)[-1600:]
    return result.returncode, message


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--head', default='HEAD')
    ap.add_argument('--expected-guard-canonical-ast-sha256', required=True)
    ap.add_argument('--expected-audit-canonical-ast-sha256', required=True)
    args = ap.parse_args()
    try:
        expected_guard_canonical_ast_sha256 = _require_external_canonical_ast_sha256(
            args.expected_guard_canonical_ast_sha256,
            label='expected guard canonical AST SHA-256',
        )
        expected_audit_canonical_ast_sha256 = _require_external_canonical_ast_sha256(
            args.expected_audit_canonical_ast_sha256,
            label='expected audit canonical AST SHA-256',
        )
    except GuardError as exc:
        ap.error(str(exc))

    files = changed_files(args.base, args.head)
    checked = []
    blockers: list[str] = []
    helpers = []

    # These expected identities are supplied by the independently reviewed workflow.
    # There is deliberately no self-hash or repository-local fallback.
    blockers.extend(
        guard_module_blockers(
            Path(HDA_GUARD_PATH),
            expected_canonical_ast_sha256=expected_guard_canonical_ast_sha256,
        )
    )
    blockers.extend(
        hda_audit_module_blockers(
            Path(HDA_AUDIT_PATH),
            expected_canonical_ast_sha256=expected_audit_canonical_ast_sha256,
        )
    )

    active = set(active_v2_contracts())
    runner_map = all_contract_runners()

    for file_name in files:
        path = Path(file_name)
        if not path.exists():
            continue

        if file_name == ZERO_LABEL_HDA_PATH:
            blockers.extend(zero_label_hda_blockers(path))
        if file_name == HDA_SCORING_PATH:
            blockers.extend(scoring_module_blockers(path))

        if file_name.startswith('.github/workflows/'):
            if not path.name.startswith('football3-'):
                blockers.append(f'{file_name}: football3 branch may modify only football3-* workflows')
            continue

        if not file_name.startswith(SCIENTIFIC_CODE_PREFIXES):
            continue
        if path.suffix in BLOCKED_EXECUTABLE_SUFFIXES:
            blockers.append(f'{file_name}: alternate executable scientific surface is not allowed under V2; migrate to reviewed Python')
            continue
        if path.suffix != '.py':
            continue

        # A historical infrastructure exemption is never permission to change production
        # code. Every changed exempt production module must have a dedicated executable
        # AST contract; only the synthetic HDA test file may contain adversarial payloads.
        if (
            file_name in EXEMPT_EXACT
            and file_name not in DEDICATED_EXEMPT_PRODUCTION_CONTRACTS
            and file_name not in EXEMPT_TEST_ONLY_PATHS
        ):
            blockers.append(
                f'{file_name}: EXEMPT_PRODUCTION_CHANGE_REQUIRES_DEDICATED_AST_CONTRACT'
            )

        try:
            cp = contract_constant(path)
            hp = helper_contract_constant(path)
        except SyntaxError as exc:
            blockers.append(f'{file_name}: syntax error: {exc}')
            continue

        # Dynamic execution/import/reflection authority is a hard blocker before any
        # infrastructure exemption. Synthetic tests are the only surface allowed to hold
        # malicious examples, and those examples are parsed by the guard rather than executed.
        if file_name != HDA_TEST_PATH:
            for reason in dynamic_authority_blockers(path):
                blockers.append(f'{file_name}: AST_DYNAMIC_AUTHORITY_DENIED: {reason}')

        # Direct HDA-scoring references still require an explicit V2 caller/helper contract.
        scoring_ref = references_hda_scoring(path)
        if scoring_ref and file_name not in {HDA_SCORING_PATH, HDA_TEST_PATH} and not (cp or hp):
            blockers.append(
                f'{file_name}: HDA scoring reference must declare FOOTBALL3_EXPERIMENT_CONTRACT or FOOTBALL3_EXPERIMENT_HELPER_FOR'
            )

        if file_name == HDA_SCORING_PATH or is_infra_python(file_name):
            continue

        if cp and hp:
            blockers.append(f'{file_name}: cannot be both scoring runner and experiment helper')
            continue
        if cp:
            if not file_name.startswith('football-data/research/'):
                blockers.append(f'{file_name}: scoring runner must live under football-data/research')
                continue
            try:
                cpath = _safe_contract_path(cp)
            except GuardError as exc:
                blockers.append(f'{file_name}: {exc}')
                continue
            if cpath not in active:
                blockers.append(f'{file_name}: scoring runner binds missing/non-V2 active contract {cpath}')
        elif hp:
            try:
                cpath = _safe_contract_path(hp)
            except GuardError as exc:
                blockers.append(f'{file_name}: {exc}')
                continue
            if cpath not in active:
                blockers.append(f'{file_name}: helper binds missing/non-V2 active contract {cpath}')
            else:
                helpers.append({'helper': file_name, 'contract': cpath.as_posix()})
        else:
            blockers.append(
                f'{file_name}: changed executable Python under football-data/scripts must declare FOOTBALL3_EXPERIMENT_CONTRACT or FOOTBALL3_EXPERIMENT_HELPER_FOR'
            )

    for cpath in sorted(active):
        runners = runner_map.get(cpath, [])
        if not runners:
            blockers.append(f'{cpath}: active V2 contract has no runner declaring it')
            continue
        if len(runners) != 1:
            blockers.append(f'{cpath}: active V2 contract must have exactly one scoring runner, found {len(runners)}')
        for runner in runners:
            rc, message = run_preflight(runner, cpath)
            checked.append({'runner': runner.as_posix(), 'contract': cpath.as_posix(), 'preflight_returncode': rc})
            if rc != 0:
                blockers.append(f'{runner}: V2 preflight failed for {cpath}: {message}')

    output = {
        'status': 'PASS' if not blockers else 'BLOCK',
        'changed_files': files,
        'active_v2_contract_count': len(active),
        'scientific_runners_checked': checked,
        'experiment_helpers_bound': helpers,
        'blockers': blockers,
    }
    print(json.dumps(output, indent=2))
    if blockers:
        raise SystemExit(2)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
