# -*- coding: utf-8 -*-
from __future__ import with_statement
import re
import os
import sys
import stat
from subprocess import Popen, PIPE
import shutil
from tempfile import mkdtemp
try:
    # The 'demandimport' breaks pyflakes and flake8._pyflakes
    from mercurial import demandimport
    demandimport.disable()
except ImportError:
    pass
try:
    from configparser import ConfigParser
except ImportError:   # Python 2
    from ConfigParser import ConfigParser

from flake8.engine import get_parser, get_style_guide
from flake8.main import DEFAULT_CONFIG


def git_hook(complexity=-1, strict=False, ignore=None, lazy=False):
    """This is the function used by the git hook.

    :param int complexity: (optional), any value > 0 enables complexity
        checking with mccabe
    :param bool strict: (optional), if True, this returns the total number of
        errors which will cause the hook to fail
    :param str ignore: (optional), a comma-separated list of errors and
        warnings to ignore
    :param bool lazy: (optional), allows for the instances where you don't add
        the files to the index before running a commit, e.g., git commit -a
    :returns: total number of errors if strict is True, otherwise 0
    """
    gitcmd = "git diff-index --cached --name-only --diff-filter=ACMRTUXB HEAD"
    if lazy:
        # Catch all files, including those not added to the index
        gitcmd = gitcmd.replace('--cached ', '')

    if hasattr(ignore, 'split'):
        ignore = ignore.split(',')

    # Returns the exit code, list of files modified, list of error messages
    _, files_modified, _ = run(gitcmd)
    files_modified = [f for f in files_modified if f.endswith('.py')]

    flake8_style = get_style_guide(
        config_file=DEFAULT_CONFIG, ignore=ignore, max_complexity=complexity)

    # Copy staged versions to temporary directory
    tmpdir = mkdtemp()
    files_to_check = []
    coding_pattern = r'coding[=:]\s*([-\w.]+)'
    try:
        for file_ in files_modified:
            # try to detect encoding of python file according to PEP 0263
            with open(file_) as fh:
                for i_ in xrange(2):
                    line = fh.readline()
                    match = re.search(coding_pattern, line)
                    if match:
                        coding = match.groups()[0]
                        break
                else:
                    coding = None
            # get the staged version of the file
            gitcmd_getstaged = ["git", "show", ":%s" % file_]
            _, out, _ = run(gitcmd_getstaged, raw_output=True, coding=coding)
            # write the staged version to temp dir with its full path to
            # avoid overwriting files with the same name
            dirname, filename = os.path.split(os.path.abspath(file_))
            prefix = os.path.commonprefix([dirname, tmpdir])
            dirname = os.path.relpath(dirname, start=prefix)
            dirname = os.path.join(tmpdir, dirname)
            if not os.path.isdir(dirname):
                os.makedirs(dirname)
            filename = os.path.join(dirname, filename)
            # encode for output if we know the encoding
            if coding:
                out = out.encode(coding)
            # write staged version of file to temporary directory
            with open(filename, "wb") as fh:
                fh.write(out)
        files_to_check.append(filename)
        # Run the checks
        report = flake8_style.check_files(files_to_check)
    # remove temporary directory
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    if strict:
        return report.total_errors

    return 0


def hg_hook(ui, repo, **kwargs):
    """This is the function executed directly by Mercurial as part of the
    hook. This is never called directly by the user, so the parameters are
    undocumented. If you would like to learn more about them, please feel free
    to read the official Mercurial documentation.
    """
    complexity = ui.config('flake8', 'complexity', default=-1)
    strict = ui.configbool('flake8', 'strict', default=True)
    config = ui.config('flake8', 'config', default=True)
    if config is True:
        config = DEFAULT_CONFIG

    paths = _get_files(repo, **kwargs)

    flake8_style = get_style_guide(
        config_file=config, max_complexity=complexity)
    report = flake8_style.check_files(paths)

    if strict:
        return report.total_errors

    return 0


def run(command, raw_output=False, coding=None):
    if isinstance(command, basestring):
        command = command.split()
    p = Popen(command, stdout=PIPE, stderr=PIPE)
    (stdout, stderr) = p.communicate()
    # On python 3, subprocess.Popen returns bytes objects which expect
    # endswith to be given a bytes object or a tuple of bytes but not native
    # string objects. This is simply less mysterious than using b'.py' in the
    # endswith method. That should work but might still fail horribly.
    if hasattr(stdout, 'decode'):
        if coding:
            stdout = stdout.decode(coding)
        else:
            stdout = stdout.decode()
    if hasattr(stderr, 'decode'):
        if coding:
            stderr = stderr.decode(coding)
        else:
            stderr = stderr.decode()
    if not raw_output:
        stdout = [line.strip() for line in stdout.splitlines()]
        stderr = [line.strip() for line in stderr.splitlines()]
    return (p.returncode, stdout, stderr)


def _get_files(repo, **kwargs):
    seen = set()
    for rev in range(repo[kwargs['node']], len(repo)):
        for file_ in repo[rev].files():
            file_ = os.path.join(repo.root, file_)
            if file_ in seen or not os.path.exists(file_):
                continue
            seen.add(file_)
            if file_.endswith('.py'):
                yield file_


def find_vcs():
    if os.path.isdir('.git'):
        if not os.path.isdir('.git/hooks'):
            os.mkdir('.git/hooks')
        return '.git/hooks/pre-commit'
    elif os.path.isdir('.hg'):
        return '.hg/hgrc'
    return ''


git_hook_file = """#!/usr/bin/env python
import sys
import os
from flake8.hooks import git_hook

COMPLEXITY = os.getenv('FLAKE8_COMPLEXITY', 10)
STRICT = os.getenv('FLAKE8_STRICT', False)


if __name__ == '__main__':
    sys.exit(git_hook(complexity=COMPLEXITY, strict=STRICT))
"""


def _install_hg_hook(path):
    if not os.path.isfile(path):
        # Make the file so we can avoid IOError's
        open(path, 'w+').close()

    c = ConfigParser()
    c.readfp(open(path, 'r'))
    if not c.has_section('hooks'):
        c.add_section('hooks')

    if not c.has_option('hooks', 'commit'):
        c.set('hooks', 'commit', 'python:flake8.hooks.hg_hook')

    if not c.has_option('hooks', 'qrefresh'):
        c.set('hooks', 'qrefresh', 'python:flake8.hooks.hg_hook')

    if not c.has_section('flake8'):
        c.add_section('flake8')

    if not c.has_option('flake8', 'complexity'):
        c.set('flake8', 'complexity', str(os.getenv('FLAKE8_COMPLEXITY', 10)))

    if not c.has_option('flake8', 'strict'):
        c.set('flake8', 'strict', os.getenv('FLAKE8_STRICT', False))

    c.write(open(path, 'w+'))


def install_hook():
    vcs = find_vcs()

    if not vcs:
        p = get_parser()
        sys.stderr.write('Error: could not find either a git or mercurial '
                         'directory. Please re-run this in a proper '
                         'repository.')
        p.print_help()
        sys.exit(1)

    status = 0
    if 'git' in vcs:
        with open(vcs, 'w+') as fd:
            fd.write(git_hook_file)
        # rwxr--r--
        os.chmod(vcs, stat.S_IRWXU | stat.S_IRGRP | stat.S_IROTH)
    elif 'hg' in vcs:
        _install_hg_hook(vcs)
    else:
        status = 1

    sys.exit(status)
