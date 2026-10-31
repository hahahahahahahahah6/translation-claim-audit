"""Install each local distribution into a fresh venv and verify externally.

Run with python3 tools/verify_dist.py. Fixtures and venvs are temporary.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import venv

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = 'from transformers import pipeline\noperation = pipeline("translation")\n'


def verify(dist):
    with tempfile.TemporaryDirectory(prefix='translation-audit-install-') as temporary:
        base = Path(temporary)
        env = base/'venv'
        venv.EnvBuilder(with_pip=True).create(env)
        interpreter = env/'bin/python3'
        environment = os.environ.copy()
        environment.pop('PYTHONPATH', None)
        subprocess.run([str(interpreter), '-m', 'pip', 'install', str(dist)],
                       cwd=base, env=environment, check=True, capture_output=True, text=True)
        fixture_data = {
            'good': ('This tool translates text.', 'main.py', EVIDENCE),
            'bad': ('This tool translates text.', 'main.py', 'value = "bonjour"\n'),
            'testonly': ('This tool translates text.', 'tests/test_main.py', EVIDENCE),
            'no-translation': ('There is no translation.', 'main.py', EVIDENCE),
        }
        for name, (doc, filename, source) in fixture_data.items():
            project = base/name
            (project/filename).parent.mkdir(parents=True, exist_ok=True)
            (project/'README.md').write_text(doc)
            (project/filename).write_text(source)
        expected = {
            'good': (0, []),
            'bad': (2, [{'id': 'documented-translation-claim-false', 'severity': 'HIGH'}]),
            'testonly': (0, [{'id': 'documented-translation-claim-false', 'severity': 'MEDIUM'}]),
            'no-translation': (2, [{'id': 'documented-no-translation-claim-false', 'severity': 'HIGH'}]),
        }
        checks = []
        for command in ([str(env/'bin/translation-claim-audit')],
                        [str(interpreter), '-m', 'translation_claim_audit']):
            def run(*args):
                return subprocess.run(command + list(args), cwd=base, env=environment,
                                      capture_output=True, text=True)
            version = run('--version')
            assert (version.returncode, version.stdout.strip()) == (0, '0.1.0'), version
            for name, (code, findings) in expected.items():
                result = run(str(base/name), '--json')
                data = json.loads(result.stdout)
                assert result.returncode == code, result
                assert data['findings'] == findings, data
                assert data['status'] == ('FAIL' if findings else 'PASS'), data
                checks.append(name + ': ' + str(code))
            result = run(str(base/'testonly'), '--fail-on', 'MEDIUM', '--json')
            assert result.returncode == 2 and json.loads(result.stdout)['findings'] == expected['testonly'][1]
            result = run(str(ROOT), '--json')
            assert result.returncode == 0 and json.loads(result.stdout)['status'] == 'PASS', result
            invalid = run(str(base/'missing'))
            assert invalid.returncode == 1, invalid
            checks += ['testonly --fail-on MEDIUM: 2', 'self-audit: PASS', '--version: 0.1.0', 'invalid root: 1']
        # Verify imports actually came from the environment, not the checkout.
        result = subprocess.run([str(interpreter), '-c',
                                 'import translation_claim_audit; print(translation_claim_audit.__file__)'],
                                cwd=base, env=environment, capture_output=True, text=True, check=True)
        assert str(env) in result.stdout, result.stdout
        print(json.dumps({'distribution': dist.name, 'both_entry_points': 'PASS', 'checks': checks}))


if __name__ == '__main__':
    distributions = sorted((ROOT/'dist').glob('*'))
    assert len(distributions) == 2
    for distribution in distributions:
        verify(distribution)
