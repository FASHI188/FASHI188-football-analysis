from __future__ import annotations

import argparse
import ast
import json
import subprocess
from pathlib import Path

SCIENCE_DIR = Path('football-data/research')
CONTRACT_TEMPLATE = SCIENCE_DIR / 'FOOTBALL3_EXPERIMENT_CONTRACT_TEMPLATE_V2.json'
ZERO_LABEL_HDA_PATH = 'football-data/research/football3_hda.py'
HDA_SCORING_PATH = 'football-data/research/football3_hda_scoring.py'
HDA_TEST_PATH = 'football-data/research/test_football3_hda.py'
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
    '_max_specific_score': (('cells', 'tie_tolerance'), ()),
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


def references_hda_scoring(path: Path) -> bool:
    """Fail closed on direct, reflective, or dynamically constructed scoring references.

    Dynamic import/eval/exec/compile/getattr surfaces are authority-bearing because a static
    guard cannot prove that their runtime target excludes HDA scoring.  This check happens
    before EXEMPT_EXACT, so infrastructure files do not receive a caller-contract bypass.
    """
    try:
        tree = _parse(path)
    except SyntaxError:
        return False

    bindings = _static_string_bindings(tree)
    importlib_aliases = {'importlib'}
    builtins_aliases = {'builtins', '__builtins__'}
    dynamic_import_names = {'import_module', '__import__'}
    reflective_names = {'eval', 'exec', 'compile', 'getattr'}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == HDA_SCORING_MODULE:
                    return True
                if alias.name == 'importlib':
                    importlib_aliases.add(alias.asname or alias.name)
                if alias.name == 'builtins':
                    builtins_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module == HDA_SCORING_MODULE:
                return True
            if node.module == 'importlib':
                for alias in node.names:
                    if alias.name == 'import_module':
                        dynamic_import_names.add(alias.asname or alias.name)
            if node.module == 'builtins':
                for alias in node.names:
                    bound = alias.asname or alias.name
                    if alias.name == '__import__':
                        dynamic_import_names.add(bound)
                    elif alias.name in {'eval', 'exec', 'compile', 'getattr'}:
                        reflective_names.add(bound)

    # Propagate simple callable aliases such as loader = load_module or loader = __import__.
    alias_assignments: list[tuple[str, ast.AST]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                alias_assignments.append((target.id, node.value))
    for _ in range(len(alias_assignments) + 1):
        changed = False
        for target, value in alias_assignments:
            source: str | None = None
            if isinstance(value, ast.Name) and value.id in importlib_aliases and target not in importlib_aliases:
                importlib_aliases.add(target)
                changed = True
            if isinstance(value, ast.Name) and value.id in builtins_aliases and target not in builtins_aliases:
                builtins_aliases.add(target)
                changed = True
            if isinstance(value, ast.Name) and value.id in dynamic_import_names | reflective_names:
                source = value.id
            elif (
                isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id in importlib_aliases
                and value.attr == 'import_module'
            ):
                source = 'import_module'
            elif (
                isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id in builtins_aliases
                and value.attr in {'__import__', 'eval', 'exec', 'compile', 'getattr'}
            ):
                source = value.attr
            elif (
                isinstance(value, ast.Subscript)
                and isinstance(value.value, ast.Name)
                and value.value.id in builtins_aliases
                and isinstance(value.slice, ast.Constant)
                and value.slice.value in {'__import__', 'eval', 'exec', 'compile', 'getattr'}
            ):
                source = str(value.slice.value)
            if source is not None and target not in dynamic_import_names and target not in reflective_names:
                if source in reflective_names:
                    reflective_names.add(target)
                else:
                    dynamic_import_names.add(target)
                changed = True
        if not changed:
            break

    # Any reference to a dynamic import or reflective built-in through a known
    # module alias is authority-bearing, even when it is stored in a conditional
    # expression or another callable container before invocation.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and (
                (node.value.id in importlib_aliases and node.attr == 'import_module')
                or (
                    node.value.id in builtins_aliases
                    and node.attr in {'__import__', 'eval', 'exec', 'compile', 'getattr'}
                )
            )
        ):
            return True
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id in builtins_aliases
            and isinstance(node.slice, ast.Constant)
            and node.slice.value in {'__import__', 'eval', 'exec', 'compile', 'getattr'}
        ):
            return True

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_dynamic_import = (
            isinstance(func, ast.Name) and func.id in dynamic_import_names
        ) or (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in importlib_aliases
            and func.attr == 'import_module'
        )
        if is_dynamic_import:
            # Unresolved dynamic module names are authority-bearing too: fail closed.
            return True
        if isinstance(func, ast.Name) and func.id in reflective_names:
            return True
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in builtins_aliases
            and func.attr in {'__import__', 'eval', 'exec', 'compile', 'getattr'}
        ):
            return True
        if (
            isinstance(func, ast.Subscript)
            and isinstance(func.value, ast.Name)
            and func.value.id in builtins_aliases
            and isinstance(func.slice, ast.Constant)
            and func.slice.value in {'__import__', 'eval', 'exec', 'compile', 'getattr'}
        ):
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
    args = ap.parse_args()
    files = changed_files(args.base, args.head)
    checked = []
    blockers: list[str] = []
    helpers = []

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

        try:
            cp = contract_constant(path)
            hp = helper_contract_constant(path)
        except SyntaxError as exc:
            blockers.append(f'{file_name}: syntax error: {exc}')
            continue

        # Any scoring import path, alias, re-export, getattr route or dynamic import string
        # is authority-bearing outside the audited pure scoring module and synthetic tests.
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
