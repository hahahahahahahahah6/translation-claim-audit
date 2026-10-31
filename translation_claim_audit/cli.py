"""Command line interface."""
import argparse
import json
import sys
from . import __version__
from .audit import audit


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, self.prog + ': error: ' + message + '\n')


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument('root', nargs='?', default='.')
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('--json', action='store_true', dest='as_json')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--ignore', action='append', default=[], metavar='GLOB', help='Exclude matching relative paths; repeatable')
    parser.add_argument('--fail-on', choices=['HIGH', 'MEDIUM'], default='HIGH')
    args = parser.parse_args(argv)
    try:
        result = audit(args.root, args.ignore)
    except (OSError, UnicodeError, ValueError) as error:
        print('translation-claim-audit: ' + str(error), file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        print(result['status'])
        for finding in result['findings']:
            print(finding['severity'] + ' ' + finding['id'])
        if args.verbose:
            print('Production evidence: ' + str(result['production_evidence']))
            print('Test evidence: ' + str(result['test_evidence']))
            print('Claims: ' + repr(result['translation_claims']))
            print('Negative claims: ' + repr(result['no_translation_claims']))
    return 2 if any(f['severity'] == 'HIGH' or args.fail_on == 'MEDIUM' for f in result['findings']) else 0
