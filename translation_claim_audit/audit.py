"""Documentation matching and conservative, provider-resolved AST evidence."""
import ast
import fnmatch
import re
from pathlib import Path
from .phrases import TRANSLATION, NOTRANSLATION

MODELS = {'MarianMTModel', 'MBartForConditionalGeneration',
          'M2M100ForConditionalGeneration', 'NllbModel', 'NllbForConditionalGeneration'}
DEEP = {'GoogleTranslator', 'MicrosoftTranslator', 'MyMemoryTranslator',
        'LingueeTranslator', 'PonsTranslator', 'YandexTranslator',
        'DeepLTranslator', 'QcriTranslator'}


def normalize(text):
    return ' '.join(text.lower().replace("n't", ' not').replace('-', ' ').split())


def matches(text, phrases):
    pattern = '|'.join(re.escape(p) for p in sorted(phrases, key=lambda p: (-len(p), p)))
    return list(re.finditer(r'\b(?:' + pattern + r')\b', text))


def paragraphs(text):
    lines = []
    fence = None
    for line in text.splitlines():
        if fence:
            if re.fullmatch(r' {0,3}' + re.escape(fence[0]) + '{' + str(fence[1]) + r',}\s*', line):
                fence = None
            lines.append('')
            continue
        opening = re.match(r' {0,3}(`{3,}|~{3,})(.*)$', line)
        if opening and not (opening[1][0] == '`' and '`' in opening[2]):
            fence = (opening[1][0], len(opening[1]))
            lines.append('')
        else:
            lines.append(line)
    return [normalize(p) for p in re.split(r'\n\s*\n', '\n'.join(lines)) if p.strip()]


def claims(text):
    positive, negative = [], []
    for paragraph in paragraphs(text):
        spans = matches(paragraph, NOTRANSLATION)
        negative.extend(m.group() for m in spans)
        chars = list(paragraph)
        for m in spans:
            chars[m.start():m.end()] = ' ' * (m.end()-m.start())
        positive.extend(m.group() for m in matches(''.join(chars), TRANSLATION))
    return positive, negative


class Evidence(ast.NodeVisitor):
    def __init__(self):
        self.bindings = {}
        self.found = False

    def resolve(self, node):
        if isinstance(node, ast.Name):
            return self.bindings.get(node.id, '')
        if isinstance(node, ast.Attribute):
            base = self.resolve(node.value)
            return base + '.' + node.attr if base else ''
        return ''

    def object_kind(self, node):
        if isinstance(node, ast.Name):
            value = self.bindings.get(node.id, '')
            return value[1:] if value.startswith('@') else ''
        if isinstance(node, ast.Call):
            name = self.resolve(node.func)
            provider, _, leaf = name.rpartition('.')
            if provider == 'googletrans' and leaf == 'Translator':
                return 'translate'
            if provider == 'deep_translator' and leaf in DEEP:
                return 'translate'
            if provider == 'deepl' and leaf == 'Translator':
                return 'translate_text'
        return ''

    def visit_Import(self, node):
        for alias in node.names:
            self.bindings[alias.asname or alias.name.split('.')[0]] = alias.name if alias.asname else alias.name.split('.')[0]

    def visit_ImportFrom(self, node):
        for alias in node.names:
            self.bindings[alias.asname or alias.name] = (node.module + '.' + alias.name) if node.module and not node.level else ''

    def bind(self, target, value=''):
        if isinstance(target, ast.Name):
            self.bindings[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self.bind(item)

    def visit_Assign(self, node):
        self.visit(node.value)
        kind = self.object_kind(node.value)
        value = '@' + kind if kind else self.resolve(node.value)
        for target in node.targets:
            self.bind(target, value)

    def visit_AnnAssign(self, node):
        if node.value:
            self.visit(node.value)
        kind = self.object_kind(node.value)
        self.bind(node.target, '@' + kind if kind else self.resolve(node.value))

    def visit_AugAssign(self, node):
        self.visit(node.value)
        self.bind(node.target)

    def visit_Call(self, node):
        name = self.resolve(node.func)
        provider, _, leaf = name.rpartition('.')
        if provider == 'transformers':
            if leaf in MODELS:
                self.found = True
            if leaf == 'pipeline':
                tasks = list(node.args[:1]) + [k.value for k in node.keywords if k.arg == 'task']
                if any(isinstance(t, ast.Constant) and isinstance(t.value, str) and
                       re.search(r'(?<![a-z])translation(?![a-z])', t.value.lower().replace('-', ' ')) for t in tasks):
                    self.found = True
        if isinstance(node.func, ast.Attribute):
            base = self.resolve(node.func.value)
            mod, _, model = base.rpartition('.')
            if node.func.attr == 'from_pretrained' and mod == 'transformers' and model in MODELS:
                self.found = True
            if node.func.attr == self.object_kind(node.func.value):
                self.found = True
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        # Python determines local names for the entire function, including
        # assignments after a call. Keep those locals out of imported bindings.
        for decorator in node.decorator_list:
            self.visit(decorator)
        for value in node.args.defaults + [v for v in node.args.kw_defaults if v]:
            self.visit(value)
        self.bindings[node.name] = ''
        saved = self.bindings.copy()
        collector = LocalNames()
        for child in node.body:
            collector.visit(child)
        for name in collector.names - collector.external:
            self.bindings[name] = ''
        for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
            self.bindings[arg.arg] = ''
        for arg in (node.args.vararg, node.args.kwarg):
            if arg:
                self.bindings[arg.arg] = ''
        for child in node.body:
            self.visit(child)
        self.bindings = saved

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.bindings[node.name] = ''
        saved = self.bindings.copy()
        for child in node.body:
            self.visit(child)
        self.bindings = saved

    def visit_Lambda(self, node):
        saved = self.bindings.copy()
        for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
            self.bindings[arg.arg] = ''
        for arg in (node.args.vararg, node.args.kwarg):
            if arg:
                self.bindings[arg.arg] = ''
        self.visit(node.body)
        self.bindings = saved

    def comprehension(self, node):
        saved = self.bindings.copy()
        for generator in node.generators:
            self.visit(generator.iter)
            self.bind(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)
        self.bindings = saved

    visit_ListComp = comprehension
    visit_SetComp = comprehension
    visit_DictComp = comprehension
    visit_GeneratorExp = comprehension

    def visit_For(self, node):
        self.visit(node.iter)
        self.bind(node.target)
        for child in node.body + node.orelse:
            self.visit(child)

    visit_AsyncFor = visit_For

    def visit_With(self, node):
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars:
                self.bind(item.optional_vars)
        for child in node.body:
            self.visit(child)

    visit_AsyncWith = visit_With

    def visit_NamedExpr(self, node):
        self.visit(node.value)
        kind = self.object_kind(node.value)
        self.bind(node.target, '@' + kind if kind else self.resolve(node.value))

    def visit_Delete(self, node):
        for target in node.targets:
            self.bind(target)


class LocalNames(ast.NodeVisitor):
    """Collect lexical locals without descending into nested scopes."""
    def __init__(self):
        self.names = set()
        self.external = set()

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_Import(self, node):
        for alias in node.names:
            self.names.add(alias.asname or alias.name.split('.')[0])

    def visit_ImportFrom(self, node):
        for alias in node.names:
            self.names.add(alias.asname or alias.name)

    def visit_FunctionDef(self, node):
        self.names.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Lambda(self, node):
        pass

    def visit_Global(self, node):
        self.external.update(node.names)

    visit_Nonlocal = visit_Global

    def visit_ListComp(self, node):
        pass

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp


def source_evidence(text):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return False
    visitor = Evidence()
    visitor.visit(tree)
    return visitor.found


def audit(root, ignore=()):
    root = Path(root)
    if not root.is_dir():
        raise ValueError('Root must be an existing directory')
    docs, production, tests = [], [], []
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root.parent):
            continue
        if not path.is_file() or any(fnmatch.fnmatch(relative.as_posix(), p) for p in ignore):
            continue
        parts = relative.parts
        if relative.as_posix() == 'README.md' or (path.suffix == '.md' and 'docs' in parts[:-1]):
            docs.append(path)
        if path.suffix == '.py' and not {'fixtures', 'docs', 'examples'}.intersection(parts[:-1]):
            target = tests if 'tests' in parts[:-1] or path.name.startswith('test_') or path.name.endswith('_test.py') else production
            target.append(path)
    positive, negative = [], []
    for path in docs:
        pos, neg = claims(path.read_text(encoding='utf-8'))
        positive.extend(pos)
        negative.extend(neg)
    prod = any(source_evidence(p.read_text(encoding='utf-8')) for p in production)
    test = any(source_evidence(p.read_text(encoding='utf-8')) for p in tests)
    findings = []
    if positive and not prod:
        findings.append({'id': 'documented-translation-claim-false',
                         'severity': 'MEDIUM' if test else 'HIGH'})
    if negative and prod:
        findings.append({'id': 'documented-no-translation-claim-false', 'severity': 'HIGH'})
    return {'status': 'FAIL' if findings else 'PASS', 'findings': findings,
            'production_evidence': prod, 'test_evidence': test,
            'translation_claims': positive, 'no_translation_claims': negative}
