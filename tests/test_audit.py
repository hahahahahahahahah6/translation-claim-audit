import contextlib
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from translation_claim_audit.audit import (audit, claims, normalize, paragraphs,
                                           source_evidence, MODELS, DEEP)
from translation_claim_audit.phrases import TRANSLATION, NOTRANSLATION
from translation_claim_audit.cli import main


class Phrases(unittest.TestCase):
    def test_counts_and_uniqueness(self):
        self.assertEqual((len(TRANSLATION), len(NOTRANSLATION)), (50, 26))
        self.assertEqual((len(set(TRANSLATION)), len(set(NOTRANSLATION))), (50, 26))

    def test_every_positive(self):
        for phrase in TRANSLATION:
            with self.subTest(phrase=phrase):
                self.assertEqual(claims(phrase), ([phrase], []))

    def test_every_negative(self):
        for phrase in NOTRANSLATION:
            with self.subTest(phrase=phrase):
                self.assertEqual(claims(phrase), ([], [phrase]))

    def test_normalization(self):
        self.assertEqual(normalize("DOESN'T\t support\n machine-translation"), 'does not support machine translation')
        self.assertEqual(claims("It doesn't support translation."), ([], ['does not support translation']))
        self.assertEqual(claims('REAL-TIME   TRANSLATION'), (['real time translation'], []))

    def test_boundaries(self):
        self.assertEqual(claims('untranslates texts; translation supporter'), ([], []))
        self.assertEqual(claims('(translate text)'), (['translate text'], []))

    def test_longest(self):
        self.assertEqual(claims('translates text into French'), (['translates text into'], []))
        self.assertEqual(claims('neural machine translation'), (['neural machine translation'], []))

    def test_paragraphs(self):
        self.assertEqual(claims('translate\ntext'), (['translate text'], []))
        self.assertEqual(claims('translate\n \ntext'), ([], []))
        self.assertEqual(len(paragraphs('a\n\nb\n\n c')), 3)

    def test_fences(self):
        for fence in ('```', '~~~', '   ````', '  ~~~~'):
            with self.subTest(fence=fence):
                self.assertEqual(claims(fence+'py\ntranslate text\n'+fence), ([], []))
        self.assertEqual(claims('```\ntranslate text'), ([], []))
        self.assertEqual(claims('````\n```\ntranslate text\n````'), ([], []))
        self.assertEqual(claims('    ```\ntranslate text\n    ```'), (['translate text'], []))

    def test_inline(self):
        self.assertEqual(claims('It can `translate text`.'), (['translate text'], []))

    def test_removal(self):
        self.assertEqual(claims('no machine translation; translate text'), (['translate text'], ['no machine translation']))
        self.assertEqual(claims('translate no translation text'), ([], ['no translation']))


class Source(unittest.TestCase):
    def yes(self, code):
        self.assertTrue(source_evidence(code), code)

    def no(self, code):
        self.assertFalse(source_evidence(code), code)

    def test_pipeline_forms(self):
        for prefix, func in [('import transformers', 'transformers.pipeline'),
                             ('import transformers as t', 't.pipeline'),
                             ('from transformers import pipeline', 'pipeline'),
                             ('from transformers import pipeline as p', 'p')]:
            for arg in ["'translation'", "task='TRANSLATION-en-fr'", "'text', task='translation'", "'translation/en-fr'", "'translation_en_to_fr'", "task='translation_en_to_de'"]:
                with self.subTest(prefix=prefix, arg=arg):
                    self.yes(prefix+'\n'+func+'('+arg+')')

    def test_models(self):
        for model in MODELS:
            for suffix in ('()', ".from_pretrained('x')"):
                for prefix, name in [('import transformers as t', 't.'+model),
                                     ('from transformers import '+model+' as M', 'M')]:
                    with self.subTest(model=model, suffix=suffix, prefix=prefix):
                        self.yes(prefix+'\n'+name+suffix)

    def test_bound_providers(self):
        for module, names, method in [('googletrans', {'Translator'}, 'translate'),
                                     ('deepl', {'Translator'}, 'translate_text'),
                                     ('deep_translator', DEEP, 'translate')]:
            for name in names:
                for prefix, ctor in [('import '+module+' as p', 'p.'+name),
                                     ('from '+module+' import '+name+' as T', 'T')]:
                    for call in [ctor+'().'+method+"('text')", 'obj = '+ctor+'()\nobj.'+method+"('text')"]:
                        with self.subTest(module=module, name=name, call=call):
                            self.yes(prefix+'\n'+call)
                    self.no(prefix+'\nobj = '+ctor+'()')

    def test_nonliteral_tasks(self):
        for task in ['task', "'retranslation'", "'summarization'", "f'translation'", 'None']:
            self.no('from transformers import pipeline\npipeline('+task+')')

    def test_wrong_providers(self):
        for name in ['pipeline', 'MarianMTModel', 'Translator', 'GoogleTranslator']:
            self.no('from unrelated import '+name+'\n'+name+"('translation')")
        self.no('pipeline("translation")')
        self.no('MarianMTModel.from_pretrained("x")')

    def test_shadowing(self):
        for shadow in ['def pipeline(x): pass', 'class pipeline: pass', 'pipeline = lambda x: x']:
            self.no('from transformers import pipeline\n'+shadow+'\npipeline("translation")')
        self.no('from transformers import pipeline\ndef f(pipeline):\n pipeline("translation")')
        self.no('from transformers import pipeline\ndef f():\n pipeline("translation")\n pipeline = None')
        self.no('from googletrans import Translator\nclass Translator: pass\nt = Translator()\nt.translate("x")')
        self.no('from googletrans import Translator\nt=Translator()\nt=None\nt.translate("x")')
        self.no('import transformers as t\nt = None\nt.pipeline("translation")')

    def test_function_scope(self):
        self.yes('from transformers import pipeline\ndef f():\n return pipeline("translation")')
        self.yes('from transformers import pipeline\ndef f(pipeline):\n pass\npipeline("translation")')
        self.yes('def f():\n from googletrans import Translator as T\n obj=T()\n obj.translate("x")')
        self.no('def f():\n from transformers import pipeline\npipeline("translation")')

    def test_alias_assignment(self):
        self.yes('import transformers as t\np=t.pipeline\np("translation")')
        self.yes('from googletrans import Translator\nx=Translator()\ny=x\ny.translate("x")')

    def test_invalid(self):
        self.no('from transformers import pipeline\npipeline("translation")\ninvalid : :')

    def test_adjacent_non_evidence(self):
        snippets = [
            'from langdetect import detect\ndetect("bonjour")',
            'import langid\nlangid.classify("bonjour")',
            'import fasttext\nfasttext.load_model("lid.bin").predict("x")',
            'import cld3\ncld3.get_language("x")',
            'import gettext\ngettext.gettext("x")',
            'from babel.dates import format_date\nformat_date(None)',
            'import locale\nlocale.setlocale(locale.LC_ALL, "fr")',
            'dictionary.get("word")', 'from unidecode import unidecode\nunidecode("x")',
            'text="bonjour"\ntext.translate({})',
            'translation="bonjour"', 'import subprocess\nsubprocess.run(["translate", "x"])',
            'import os\nos.system("translate x")', 'translator.translate("x")',
            'from googletrans import Translator\nTranslator.translate("x")',
            'from deepl import Translator\nTranslator().translate("x")',
            'from googletrans import Translator\nTranslator().detect("x")',
            'from transformers import pipeline\npipeline("automatic-speech-recognition")',
            'from transformers import pipeline\npipeline("text-to-speech")',
            'from transformers import pipeline\npipeline("audio-generation")',
            'from transformers import pipeline\npipeline("summarization")',
            'import transformers\nfrom googletrans import Translator',
        ]
        for code in snippets:
            with self.subTest(code=code):
                self.no(code)


class Project(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def put(self, name, text):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_doc_scope(self):
        for name, included in [('README.md', True), ('docs/a.md', True),
                               ('a/docs/nested/a.md', True), ('a/README.md', False),
                               ('other.md', False), ('docs/a.txt', False)]:
            path = self.put(name, 'translate text')
            with self.subTest(name=name):
                self.assertEqual(bool(audit(self.root)['findings']), included)
            path.unlink()

    def test_source_classification(self):
        self.put('README.md', 'translate text')
        for name, severity in [('main.py', None), ('tests/a.py', 'MEDIUM'),
                               ('a/test_x.py', 'MEDIUM'), ('a/x_test.py', 'MEDIUM'),
                               ('fixtures/a.py', 'HIGH'), ('docs/a.py', 'HIGH'),
                               ('examples/a.py', 'HIGH'), ('tests/fixtures/a.py', 'HIGH')]:
            path = self.put(name, 'from transformers import pipeline\npipeline("translation")')
            with self.subTest(name=name):
                result = audit(self.root)
                self.assertEqual([f['severity'] for f in result['findings']], [severity] if severity else [])
            path.unlink()

    def test_no_source_asymmetry(self):
        path = self.put('README.md', 'translate text')
        self.assertEqual(audit(self.root)['findings'], [{'id': 'documented-translation-claim-false', 'severity': 'HIGH'}])
        path.write_text('no translation')
        self.assertEqual(audit(self.root)['status'], 'PASS')
        path.write_text('nothing claimed')
        self.assertEqual(audit(self.root)['status'], 'PASS')

    def test_negative_and_single_family(self):
        self.put('README.md', 'no translation\n\nno machine translation')
        self.put('main.py', 'from transformers import MarianMTModel\nMarianMTModel()')
        self.assertEqual(audit(self.root)['findings'], [{'id': 'documented-no-translation-claim-false', 'severity': 'HIGH'}])
        self.put('README.md', 'translate text\n\ntranslate documents')
        (self.root/'main.py').unlink()
        self.assertEqual(len(audit(self.root)['findings']), 1)

    def test_testonly_negative(self):
        self.put('README.md', 'no translation')
        self.put('tests/a.py', 'from transformers import MarianMTModel\nMarianMTModel()')
        self.assertEqual(audit(self.root)['status'], 'PASS')

    def test_symlinks(self):
        target = self.put('fixtures/a.py', 'from transformers import MarianMTModel\nMarianMTModel()')
        self.put('README.md', 'translate text')
        (self.root/'main.py').symlink_to(target)
        (self.root/'linked').symlink_to(target.parent, target_is_directory=True)
        self.assertEqual(audit(self.root)['findings'][0]['severity'], 'HIGH')
        (self.root/'README.md').unlink()
        target = self.put('other.md', 'translate text')
        (self.root/'README.md').symlink_to(target)
        self.assertEqual(audit(self.root)['status'], 'PASS')

    def runcli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(list(args))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_cli(self):
        self.put('README.md', 'translate text')
        self.assertEqual(self.runcli(str(self.root))[0], 2)
        self.assertEqual(len(json.loads(self.runcli(str(self.root), '--json')[1])['findings']), 1)
        self.assertIn('Production evidence:', self.runcli(str(self.root), '--verbose')[1])
        self.assertEqual(self.runcli(str(self.root), '--ignore', 'README.md')[0], 0)
        self.put('tests/test_a.py', 'from transformers import MarianMTModel\nMarianMTModel()')
        self.assertEqual(self.runcli(str(self.root))[0], 0)
        self.assertEqual(self.runcli(str(self.root), '--fail-on', 'MEDIUM')[0], 2)
        self.assertEqual(self.runcli(str(self.root), '--fail-on', 'HIGH')[0], 0)
        self.assertEqual(self.runcli('--version')[:2], (0, '0.1.0\n'))

    def test_errors(self):
        for args in [('/not/a/real/root',), ('--unknown',), ('--fail-on', 'LOW'), ('--ignore',)]:
            self.assertEqual(self.runcli(*args)[0], 1)

    def test_ignore_source(self):
        self.put('README.md', 'translate text')
        self.put('src/main.py', 'from transformers import MarianMTModel\nMarianMTModel()')
        self.assertEqual(audit(self.root, ['src/*.py'])['findings'][0]['severity'], 'HIGH')

class ScopeRegressions(unittest.TestCase):
    def test_lambda_shadow(self):
        self.assertFalse(source_evidence('from transformers import pipeline\nf=lambda pipeline: pipeline("translation")'))

    def test_comprehension_shadow(self):
        self.assertFalse(source_evidence('from transformers import pipeline\nx=[pipeline("translation") for pipeline in items]'))

    def test_nested_definition_shadow(self):
        self.assertFalse(source_evidence('from transformers import pipeline\ndef f():\n pipeline("translation")\n def pipeline(x): pass'))

    def test_nested_scope_does_not_shadow_parent(self):
        self.assertTrue(source_evidence('from transformers import pipeline\ndef f():\n def g():\n  pipeline=None\n pipeline("translation")'))

    def test_local_import_shadows_before_import(self):
        self.assertFalse(source_evidence('from transformers import pipeline\ndef f():\n pipeline("translation")\n from other import pipeline'))

    def test_loop_shadow(self):
        self.assertFalse(source_evidence('from transformers import pipeline\nfor pipeline in items:\n pipeline("translation")'))

    def test_context_shadow(self):
        self.assertFalse(source_evidence('from transformers import pipeline\nwith context() as pipeline:\n pipeline("translation")'))

    def test_deletion_shadow(self):
        self.assertFalse(source_evidence('from transformers import pipeline\ndel pipeline\npipeline("translation")'))

    def test_no_cross_module_flow(self):
        self.assertFalse(source_evidence('from project import translator\ntranslator.translate("text")'))

    def test_production_supersedes_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'README.md').write_text('translate text')
            (root/'test_a.py').write_text('from transformers import MarianMTModel\nMarianMTModel()')
            (root/'main.py').write_text('from transformers import MarianMTModel\nMarianMTModel()')
            self.assertEqual(audit(root)['findings'], [])


if __name__ == '__main__':
    unittest.main()
