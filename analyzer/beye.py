# -*- coding: utf-8 -*-
#                     The LLVM Compiler Infrastructure
#
# This file is distributed under the University of Illinois Open Source
# License. See LICENSE.TXT for details.

import logging
import multiprocessing
import subprocess
import json
import itertools
import functools
import re
import os
from analyzer.decorators import trace, require
from analyzer.driver import run
from analyzer.report import generate_report


def main():
    """ Entry point for 'beye'.

    'beye' is orchestrating to run the analyzer against the given project
    and generates report file (if that was also requested).

    Currently it takes a compilation database as input and run analyzer
    against each files. The logic to run analyzer against a single file is
    implemented in 'analyzer.driver' module.

    Report generation logic is in a separate module called 'analyzer.report'.
    """
    multiprocessing.freeze_support()
    logging.basicConfig(format='beye: %(message)s')

    def from_number_to_level(num):
        if 0 == num:
            return logging.WARNING
        elif 1 == num:
            return logging.INFO
        elif 2 == num:
            return logging.DEBUG
        else:
            return 5  # used by the trace decorator

    def needs_report_file(opts):
        output_format = opts.get('output_format')
        return 'html' == output_format or 'plist-html' == output_format

    args = parse_command_line()

    logging.getLogger().setLevel(from_number_to_level(args['verbose']))
    logging.debug(args)

    with ReportDirectory(args['output'], args['keep_empty']) as out_dir:
        run_analyzer(args, out_dir)
        number_of_bugs = generate_report(
            {'sequential': args['sequential'],
             'out_dir': out_dir,
             'prefix': get_prefix_from(args['input']),
             'clang': args['clang'],
             'html_title': args['html_title']})\
            if needs_report_file(args) else 0
        # TODO get result from bear if --status-bugs were not requested
        return number_of_bugs if 'status_bugs' in args else 0


class ReportDirectory(object):
    """ Responsible for the report directory.

    hint -- could specify the parent directory of the output directory.
    keep -- a boolean value to keep or delete the empty report directory. """

    def __init__(self, hint, keep):
        self.name = ReportDirectory._create(hint)
        self.keep = keep

    def __enter__(self):
        return self.name

    @trace
    def __exit__(self, _type, _value, _traceback):
        if os.listdir(self.name):
            msg = "Run 'scan-view {0}' to examine bug reports."
        else:
            if self.keep:
                msg = "Report directory '{0}' contans no report, but kept."
            else:
                os.rmdir(self.name)
                msg = "Removing directory '{0}' because it contains no report."
        logging.warning(msg.format(self.name))

    @staticmethod
    def _create(hint):
        if hint != '/tmp':
            try:
                os.mkdir(hint)
                return hint
            except OSError:
                raise
        else:
            import tempfile
            return tempfile.mkdtemp(prefix='beye-', suffix='.out')


@trace
def parse_command_line():
    """ Parse command line and return a dictionary of given values.

    Command line parameters are defined by previous implementation, and
    influence either the analyzer behaviour or the report generation.
    The paramters are grouped together according their functionality.

    The help message is generated from this parse method. Default values
    are also printed. """
    from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
    parser = ArgumentParser(prog='beye',
                            formatter_class=ArgumentDefaultsHelpFormatter)
    group1 = parser.add_argument_group('options')
    group1.add_argument(
        '--input',
        metavar='<file>',
        default="compile_commands.json",
        help="The JSON compilation database.")
    group1.add_argument(
        '--output', '-o',
        metavar='<path>',
        default='/tmp',
        help='Specifies the output directory for analyzer reports.\
              Subdirectories will be created as needed to represent separate\
              "runs" of the analyzer.')
    group1.add_argument(
        '--sequential',
        action='store_true',
        help="Execute analyzer sequentialy.")
    group1.add_argument(
        '--status-bugs',
        action='store_true',
        help='By default, the exit status of ‘beye’ is the same as the\
              executed build command. Specifying this option causes the exit\
              status of ‘beye’ to be 1 if it found potential bugs and 0\
              otherwise.')
    group1.add_argument(
        '--html-title',
        metavar='<title>',
        help='Specify the title used on generated HTML pages.\
              If not specified, a default title will be used.')
    group1.add_argument(
        '--analyze-headers',
        action='store_true',
        help='Also analyze functions in #included files. By default,\
              such functions are skipped unless they are called by\
              functions within the main source file.')
    format_group = group1.add_mutually_exclusive_group()
    format_group.add_argument(
        '--plist',
        dest='output_format',
        const='plist',
        default='html',
        action='store_const',
        help='This option outputs the results as a set of .plist files.')
    format_group.add_argument(
        '--plist-html',
        dest='output_format',
        const='plist-html',
        default='html',
        action='store_const',
        help='This option outputs the results as a set of HTML and .plist\
              files.')
    group1.add_argument(
        '--verbose', '-v',
        action='count',
        default=0,
        help="Enable verbose output from ‘beye’. A second and third '-v'\
              increases verbosity.")
    # TODO: implement '-view '

    group2 = parser.add_argument_group('advanced options')
    group2.add_argument(
        '--keep-empty',
        action='store_true',
        help="Don't remove the build results directory even if no issues were\
              reported.")
    group2.add_argument(
        '--no-failure-reports',
        dest='report_failures',
        action='store_false',
        help="Do not create a 'failures' subdirectory that includes analyzer\
              crash reports and preprocessed source files.")
    group2.add_argument(
        '--stats',
        action='store_true',
        help='Generates visitation statistics for the project being analyzed.')
    group2.add_argument(
        '--internal-stats',
        action='store_true',
        help='Generate internal analyzer statistics.')
    group2.add_argument(
        '--maxloop',
        metavar='<loop count>',
        type=int,
        default=4,
        help='Specifiy the number of times a block can be visited before\
              giving up. Increase for more comprehensive coverage at a cost\
              of speed.')
    group2.add_argument(
        '--store',
        metavar='<model>',
        dest='store_model',
        default='region',
        choices=['region', 'basic'],
        help='Specify the store model used by the analyzer.\
              ‘region’ specifies a field- sensitive store model.\
              ‘basic’ which is far less precise but can more quickly\
              analyze code. ‘basic’ was the default store model for\
              checker-0.221 and earlier.')
    group2.add_argument(
        '--constraints',
        metavar='<model>',
        dest='constraints_model',
        default='range',
        choices=['range', 'basic'],
        help='Specify the contraint engine used by the analyzer. Specifying\
              ‘basic’ uses a simpler, less powerful constraint model used by\
              checker-0.160 and earlier.')
    group2.add_argument(
        '--use-analyzer',
        metavar='<path>',
        dest='clang',
        default='clang',
        help="‘beye’ uses the ‘clang’ executable relative to itself for\
              static analysis. One can override this behavior with this\
              option by using the ‘clang’ packaged with Xcode (on OS X) or\
              from the PATH.")
    group2.add_argument(
        '--analyzer-config',
        metavar='<options>',
        help="Provide options to pass through to the analyzer's\
              -analyzer-config flag. Several options are separated with comma:\
              'key1=val1,key2=val2'\
              \
              Available options:\
                stable-report-filename=true or false (default)\
                Switch the page naming to:\
                report-<filename>-<function/method name>-<id>.html\
                instead of report-XXXXXX.html")

    group3 = parser.add_argument_group('controlling checkers')
    group3.add_argument(
        '--load-plugin',
        metavar='<plugin library>',
        dest='plugins',
        action='append',
        help='Loading external checkers using the clang plugin interface.')
    group3.add_argument(
        '--enable-checker',
        metavar='<checker name>',
        action='append',
        help='Enable specific checker.')
    group3.add_argument(
        '--disable-checker',
        metavar='<checker name>',
        action='append',
        help='Disable specific checker.')

    return parser.parse_args().__dict__


@trace
@require(['input', 'sequential'])
def run_analyzer(args, out_dir):
    """ Runs the analyzer.

    The analyzer main method is written in the module 'driver:run'.
    This function calls the analyzer for each module in the compilation
    database. The method argument is a dictionary, comming from the database
    entry plus some command line paramters. The analyzer result contains
    (beside many others) the output of it, which is printed here to avoid
    non-readable output. """

    def common_params(opts):
        def uname():
            return subprocess.check_output(['uname', '-a']).decode('ascii')

        return {
            'clang': opts['clang'],
            'out_dir': out_dir,
            'direct_args': parameters_from_command_line(opts),
            'uname': uname()}

    def wrap(iterable, const):
        for current in iterable:
            current.update(const)
            yield current

    with open(args['input'], 'r') as handle:
        pool = multiprocessing.Pool(1 if args['sequential'] else None)
        for current in pool.imap_unordered(
                run, wrap(json.load(handle), common_params(args))):
            if current is not None and 'analyzer' in current:
                for line in current['analyzer']['error_output']:
                    logging.info(line.rstrip())
        pool.close()
        pool.join()


@trace
def parameters_from_command_line(args):
    """ A group of command line arguments of 'beye' can mapped to command
    line arguments of the analyzer. This method generates those. """
    opts = {k: v for k, v in args.items() if v is not None}
    result = []
    if 'store_model' in opts:
        result.append('-analyzer-store={0}'.format(opts['store_model']))
    if 'constraints_model' in opts:
        result.append(
            '-analyzer-constraints={0}'.format(opts['constraints_model']))
    if 'internal_stats' in opts:
        result.append('-analyzer-stats')
    if 'analyze_headers' in opts:
        result.append('-analyzer-opt-analyze-headers')
    if 'stats' in opts:
        result.append('-analyzer-checker=debug.Stats')
    if 'maxloop' in opts:
        result.extend(['-analyzer-max-loop', str(opts['maxloop'])])
    if 'output_format' in opts:
        result.append('-analyzer-output={0}'.format(opts['output_format']))
    if 'analyzer_config' in opts:
        result.append(opts['analyzer_config'])
    if 'verbose' in opts and 2 <= opts['verbose']:
        result.append('-analyzer-display-progress')
    if 'plugins' in opts:
        result = functools.reduce(
            lambda acc, x: acc + ['-load', x],
            opts['plugins'],
            result)
    if 'enable_checker' in opts:
        result = functools.reduce(
            lambda acc, x: acc + ['-analyzer-checker', x],
            opts['enable_checker'],
            result)
    if 'disable_checker' in opts:
        result = functools.reduce(
            lambda acc, x: acc + ['-analyzer-disable-checker', x],
            opts['disable_checker'],
            result)
    if 'ubiviz' in opts:  # TODO: never passed
        result.append('-analyzer-viz-egraph-ubigraph')
    return functools.reduce(
        lambda acc, x: acc + ['-Xclang', x], result, [])


@trace
def get_prefix_from(compilation_database):
    """ Get common path prefix for compilation database entries.
    This will be used to taylor the file names in the final report. """
    def common(files):
        result = None
        for current in files:
            result = current if result is None else\
                os.path.commonprefix([result, current])

        if result is None:
            return ''
        elif not os.path.isdir(result):
            return os.path.dirname(result)
        else:
            return result

    def filenames():
        with open(compilation_database, 'r') as handle:
            for entry in json.load(handle):
                yield os.path.dirname(entry['file'])

    return common(filenames())
