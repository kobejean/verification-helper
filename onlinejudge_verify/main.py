"""
isort: skip_file
"""

# pylint: disable=unused-import,ungrouped-imports
try:
    import onlinejudge.service
except ModuleNotFoundError:
    print("Due to a known bug, the online-judge-tools is not yet properly installed. Please re-run $ pip3 install --force-reinstall online-judge-api-client")
    exit(1)  # pylint: disable=consider-using-sys-exit
# pylint: enable=unused-import,ungrouped-imports

import tempfile
import shutil
import argparse
import json
import glob
import math
import os
import pathlib
import urllib.request
import subprocess
import sys
import textwrap
from logging import INFO, basicConfig, getLogger
from typing import *

import colorlog
import onlinejudge_verify.config
import onlinejudge_verify.documentation.main
import onlinejudge_verify.marker
import onlinejudge_verify.utils
import onlinejudge_verify.verify

logger = getLogger(__name__)


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument('--config-file', default=onlinejudge_verify.config.default_config_path, help='default: ".verify-helper/config.toml"')

    subparsers = parser.add_subparsers(dest='subcommand')

    subparser = subparsers.add_parser('all')
    subparser.add_argument('-j', '--jobs', type=int, default=1)
    subparser.add_argument('--timeout', type=float, default=600)
    subparser.add_argument('--tle', type=float, default=60)

    subparser = subparsers.add_parser('run')
    subparser.add_argument('path', nargs='*', type=pathlib.Path)
    subparser.add_argument('-j', '--jobs', type=int, default=1)
    subparser.add_argument('--timeout', type=float, default=600)
    subparser.add_argument('--tle', type=float, default=60)

    subparser = subparsers.add_parser('docs')
    subparser.add_argument('-j', '--jobs', type=int, default=1)

    subparser = subparsers.add_parser('stats')
    subparser.add_argument('-j', '--jobs', type=int, default=1)

    return parser


def subcommand_run(paths: List[pathlib.Path], *, timeout: float = 600, tle: float = 60, jobs: int = 1) -> onlinejudge_verify.verify.VerificationSummary:
    """
    :raises Exception: if test.sh fails
    """

    does_push = 'GITHUB_ACTION' in os.environ and 'GITHUB_TOKEN' in os.environ and os.environ.get('GITHUB_REF', '').startswith('refs/heads/')  # NOTE: $GITHUB_REF may be refs/pull/... or refs/tags/...
    if does_push:
        # checkout in advance to push
        branch = os.environ['GITHUB_REF'][len('refs/heads/'):]
        logger.info('$ git checkout %s', branch)
        subprocess.check_call(['git', 'checkout', branch])

    # NOTE: the GITHUB_TOKEN expires in 60 minutes (https://help.github.com/en/actions/automating-your-workflow-with-github-actions/authenticating-with-the-github_token#about-the-github_token-secret)
    # use 10 minutes as timeout for safety; 理由はよく分かってないぽいけど以前 20 分でやって死んだことがあるらしいので
    if 'GITHUB_ACTION' not in os.environ:
        timeout = math.inf

    if not paths:
        paths = sorted(list(onlinejudge_verify.utils.iterate_verification_files()))
    try:
        with onlinejudge_verify.marker.get_verification_marker() as marker:
            return onlinejudge_verify.verify.main(paths, marker=marker, timeout=timeout, tle=tle, jobs=jobs)
    finally:
        # push results even if some tests failed
        if does_push:
            pass
            # push_timestamp_to_branch()


def push_timestamp_to_branch() -> None:
    # read config
    logger.info('use GITHUB_TOKEN')  # NOTE: don't use GH_PAT here, because it may cause infinite loops with triggering GitHub Actions itself
    url = 'https://{}:{}@github.com/{}.git'.format(os.environ['GITHUB_ACTOR'], os.environ['GITHUB_TOKEN'], os.environ['GITHUB_REPOSITORY'])
    logger.info('GITHUB_ACTOR = %s', os.environ['GITHUB_ACTOR'])
    logger.info('GITHUB_REPOSITORY = %s', os.environ['GITHUB_REPOSITORY'])

    # commit and push
    subprocess.check_call(['git', 'config', '--global', 'user.name', 'GitHub'])
    subprocess.check_call(['git', 'config', '--global', 'user.email', 'noreply@github.com'])
    path = onlinejudge_verify.marker.get_verification_marker().json_path
    logger.info('$ git pull && git add %s && git commit && git push', str(path))
    subprocess.check_call(['git', 'pull'])
    if path.exists():
        subprocess.check_call(['git', 'add', str(path)])
    if subprocess.run(['git', 'diff', '--quiet', '--staged'], check=False).returncode:
        message = '[auto-verifier] verify commit {}'.format(os.environ['GITHUB_SHA'])
        subprocess.check_call(['git', 'commit', '-m', message])
        subprocess.check_call(['git', 'push', url, 'HEAD'])


def should_include_path(path: pathlib.Path, base_dir: pathlib.Path) -> bool:
    """
    Determine if a path should be included in copying operations.
    
    Args:
        path: Path to check
        base_dir: Base directory for relative path calculation
    
    Returns:
        bool: True if path should be included, False otherwise
    """
    rel_path = path.relative_to(base_dir)
    parts = rel_path.parts
    
    # Exclude .git directory and its contents
    if '.git' in parts:
        return False
        
    return True

def save_original_state(*, src_dir: pathlib.Path) -> pathlib.Path:
    """
    Save the current state of the source directory to a temporary location.
    
    Args:
        src_dir: Root directory to save
    
    Returns:
        pathlib.Path: Path to temporary directory containing the saved state
    """
    temp_dir = tempfile.mkdtemp()
    temp_path = pathlib.Path(temp_dir)
    
    logger.info('Saving original state from %s to temp directory %s', src_dir, temp_dir)
    
    # # Log initial source directory state
    # logger.info('Source directory contents before save:')
    # for path in src_dir.rglob('*'):
    #     if path.is_file() and should_include_path(path, src_dir):
    #         logger.info('- %s', path.relative_to(src_dir))
    
    # Copy all files to temp directory, including dot directories but excluding .git
    file_count = 0
    for path in src_dir.rglob('*'):
        if path.is_file() and should_include_path(path, src_dir):
            rel_path = path.relative_to(src_dir)
            dst_path = temp_path / rel_path
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(path), str(dst_path))
            file_count += 1
    
    logger.info('Saved %d files to temp directory', file_count)
    
    # # Verify temp directory contents
    # logger.info('Temp directory contents after save:')
    # for path in temp_path.rglob('*'):
    #     if path.is_file():
    #         logger.info('- %s', path.relative_to(temp_path))
    
    return temp_path

def restore_original_state(*, temp_path: pathlib.Path, src_dir: pathlib.Path) -> None:
    """
    Restore the original state from temporary directory back to source directory.
    
    Args:
        temp_path: Path to temporary directory containing saved state
        src_dir: Root directory to restore to
    """
    logger.info('Restoring original state from %s to %s', temp_path, src_dir)
    
    # # Log state before cleanup
    # logger.info('Source directory contents before restoration:')
    # for path in src_dir.rglob('*'):
    #     if path.is_file() and should_include_path(path, src_dir):
    #         logger.info('- %s', path.relative_to(src_dir))
    
    # Clean current directory
    logger.info('Cleaning directory before restoration')
    removed_count = 0
    for item in src_dir.iterdir():
        if item.name != '.git':
            if item.is_file():
                item.unlink()
                removed_count += 1
            elif item.is_dir():
                shutil.rmtree(item)
                removed_count += 1
    logger.info('Removed %d items during cleanup', removed_count)
    
    # Restore from temp directory
    restored_count = 0
    for path in temp_path.rglob('*'):
        if path.is_file():
            rel_path = path.relative_to(temp_path)
            dst_path = src_dir / rel_path
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(path), str(dst_path))
            restored_count += 1
    
    logger.info('Restored %d files', restored_count)
    
    # # Log final state
    # logger.info('Source directory contents after restoration:')
    # for path in src_dir.rglob('*'):
    #     if path.is_file() and should_include_path(path, src_dir):
    #         logger.info('- %s', path.relative_to(src_dir))
    
    # Clean up temp directory
    shutil.rmtree(temp_path)
    logger.info('Cleaned up temporary directory')

def push_documents_to_gh_pages(*, src_dir: pathlib.Path, dst_branch: str = 'gh-pages') -> None:
    """
    Push documents to GitHub Pages branch.
    
    Args:
        src_dir: Directory containing documents to push
        dst_branch: Target branch (default: 'gh-pages')
    """
    # Store original branch name
    original_branch = subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], 
                                           text=True).strip()
    logger.info('Original branch: %s', original_branch)

    # read config
    if not os.environ.get('GH_PAT'):
        logger.error("GH_PAT is not available. You cannot upload the generated documents to GitHub Pages.")
        return
    logger.info('use GH_PAT')
    url = 'https://{}@github.com/{}.git'.format(os.environ['GH_PAT'], os.environ['GITHUB_REPOSITORY'])
    logger.info('GITHUB_REPOSITORY = %s', os.environ['GITHUB_REPOSITORY'])

    # Create a temporary directory for the documents
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = pathlib.Path(temp_dir)
        
        # Copy documents to temp directory
        logger.info('Copying documents from %s to temp directory', str(src_dir))
        file_count = 0
        for path in src_dir.rglob('*'):
            if path.is_file():
                rel_path = path.relative_to(src_dir)
                dst_path = temp_path / rel_path
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(path), str(dst_path))
                file_count += 1
        logger.info('Copied %d files to temp directory', file_count)

        try:
            # checkout gh-pages
            logger.info('$ git checkout %s', dst_branch)
            try:
                subprocess.check_call(['git', 'checkout', '-f', dst_branch])
            except subprocess.CalledProcessError:
                subprocess.check_call(['git', 'checkout', '--orphan', dst_branch])
                subprocess.run(['git', 'rm', '-rf', '.'], check=False)

            # Clean directory
            logger.info('Cleaning directory for %s', dst_branch)
            for item in os.listdir('.'):
                if item != '.git':
                    path = pathlib.Path(item)
                    if path.is_file():
                        path.unlink()
                    elif path.is_dir():
                        shutil.rmtree(item)

            # Copy files from temp directory
            logger.info('Copying files from temp directory')
            copy_count = 0
            for path in temp_path.rglob('*'):
                if path.is_file():
                    rel_path = path.relative_to(temp_path)
                    rel_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(path), str(rel_path))
                    copy_count += 1
            logger.info('Copied %d files', copy_count)

            # Commit and push
            logger.info('$ git add . && git commit && git push')
            subprocess.check_call(['git', 'config', '--global', 'user.name', 'GitHub'])
            subprocess.check_call(['git', 'config', '--global', 'user.email', 'noreply@github.com'])
            subprocess.check_call(['git', 'add', '.'])
            if subprocess.run(['git', 'diff', '--quiet', '--staged'], check=False).returncode:
                message = '[auto-verifier] docs commit {}'.format(os.environ['GITHUB_SHA'])
                subprocess.check_call(['git', 'commit', '-m', message])
                subprocess.check_call(['git', 'push', url, 'HEAD'])

        finally:
            # Return to original branch
            logger.info('Returning to original branch: %s', original_branch)
            subprocess.check_call(['git', 'checkout', original_branch])


def subcommand_docs(*, jobs: int = 1) -> None:
    if 'GITHUB_ACTION' in os.environ and 'GITHUB_TOKEN' in os.environ:
        # check it is kicked by "push" event
        if os.environ['GITHUB_EVENT_NAME'] != 'push':
            logger.info('This execution is not kicked from "push" event. Updating GitHub Pages is skipped.')
            return

        # check it is on the default branch.
        try:
            # /repos/{owner}/{repo} endpoint. See https://docs.github.com/en/free-pro-team@latest/rest/reference/repos#get-a-repository
            req = urllib.request.Request(os.environ['GITHUB_API_URL'] + '/repos/' + os.environ['GITHUB_REPOSITORY'])
            req.add_header('authorization', 'Bearer ' + os.environ['GITHUB_TOKEN'])
            with urllib.request.urlopen(req) as fh:
                repos = json.loads(fh.read())
            default_branch = repos['default_branch']
        except Exception as e:
            logger.exception('failed to get the default branch: %s', e)
            logger.info('Updating GitHub Pages is skipped.')
            return
        if os.environ['GITHUB_REF'] != 'refs/heads/{}'.format(default_branch):
            logger.info('This execution is not on the default branch (the default is "refs/heads/%s" but the actual is "%s"). Updating GitHub Pages is skipped.', default_branch, os.environ['GITHUB_REF'])
            return

        # updating the GitHub Pages
        logger.info('generate documents...')
        onlinejudge_verify.documentation.main.main(jobs=jobs)

        logger.info('upload documents...')
        # Save entire working directory state
        temp_path = save_original_state(src_dir=pathlib.Path('.'))
        
        try:
            # Push markdown documents to gh-pages
            push_documents_to_gh_pages(src_dir=pathlib.Path('.verify-helper/markdown'))
        finally:
            # Restore original state regardless of success/failure
            restore_original_state(temp_path=temp_path, src_dir=pathlib.Path('.'))

    else:
        logger.info('generate documents...')
        onlinejudge_verify.documentation.main.main(jobs=jobs)
        logger.info('done.')
        logger.info('%s', '\n'.join([
            'To see the generated document, do the following steps:',
            '    1. Install Ruby with the files to build native modules. In Ubuntu, $ sudo apt install ruby-all-dev',
            "    2. Install Ruby's Bundler (https://bundler.io/). In Ubuntu, $ sudo apt install ruby-bundler",
            '    3. $ cd .verify-helper/markdown',
            '    4. $ bundle install --path .vendor/bundle',
            '    5. $ bundle exec jekyll serve --incremental',
            '    6. Open http://127.0.0.1:4000 on your web browser',
        ]))


def subcommand_stats(*, jobs: int = 1) -> None:
    onlinejudge_verify.documentation.main.print_stats_json(jobs=jobs)


def generate_gitignore() -> None:
    path = pathlib.Path('.verify-helper/.gitignore')
    data = textwrap.dedent("""\
        .gitignore
        cache/
        include/
        markdown/
        timestamps.local.json
    """)
    if path.exists():
        with open(path) as fh:
            if fh.read() == data:
                return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as fh:
        fh.write(data)


# TODO: remove this function when affected people disappears. You can see the list of such people at https://github.com/search?q=%22timestamps.local.json%22+language%3Agitignore&type=Code
def _delete_gitignore() -> None:
    """A workaround for the issue https://github.com/online-judge-tools/verification-helper/issues/332
    """
    return
    # try:
    #     # check if it's on GitHub Action
    #     should_push = 'GITHUB_ACTION' in os.environ and 'GITHUB_TOKEN' in os.environ and os.environ.get('GITHUB_REF', '').startswith('refs/heads/')
    #     if not should_push:
    #         return

    #     # checkout the target branch
    #     branch = os.environ['GITHUB_REF'][len('refs/heads/'):]
    #     logger.info('$ git checkout %s', branch)
    #     subprocess.check_call(['git', 'checkout', branch])

    #     # check if .verify-helper/.gitignore exists
    #     gitignore_path = pathlib.Path('.verify-helper', '.gitignore')
    #     gitignore_checked_in = (subprocess.run(['git', 'ls-files', '--error-unmatch', str(gitignore_path)], check=False).returncode == 0)
    #     if not gitignore_checked_in:
    #         return
    #     logger.warning('file %s exists in this Git repository. It should not be checked in.', str(gitignore_path))

    #     # read config
    #     logger.info('use GITHUB_TOKEN')  # NOTE: don't use GH_PAT here, because it may cause infinite loops with triggering GitHub Actions itself
    #     url = 'https://{}:{}@github.com/{}.git'.format(os.environ['GITHUB_ACTOR'], os.environ['GITHUB_TOKEN'], os.environ['GITHUB_REPOSITORY'])
    #     logger.info('GITHUB_ACTOR = %s', os.environ['GITHUB_ACTOR'])
    #     logger.info('GITHUB_REPOSITORY = %s', os.environ['GITHUB_REPOSITORY'])

    #     # remove .verify-helper/.gitignore
    #     subprocess.check_call(['git', 'config', '--global', 'user.name', 'GitHub'])
    #     subprocess.check_call(['git', 'config', '--global', 'user.email', 'noreply@github.com'])
    #     logger.info('$ git rm --cached %s', str(gitignore_path))
    #     subprocess.check_call(['git', 'rm', '--cached', str(gitignore_path)])
    #     message = '[auto-verifier] remove .verify-helper/.gitignore (see https://github.com/online-judge-tools/verification-helper/issues/332)'
    #     logger.info('$ git commit -m ...')
    #     subprocess.check_call(['git', 'commit', '-m', message])
    #     logger.info('$ git push ... HEAD')
    #     subprocess.check_call(['git', 'push', url, 'HEAD'])

    # except Exception:
    #     logger.exception('something wrong in _delete_gitignore(). ignored.')


def main(args: Optional[List[str]] = None) -> None:
    # configure logging
    log_format = '%(log_color)s%(levelname)s%(reset)s:%(name)s:%(message)s'
    handler = colorlog.StreamHandler()
    handler.setFormatter(colorlog.ColoredFormatter(log_format))
    basicConfig(level=INFO, handlers=[handler])

    # parse command-line arguments
    parser = get_parser()
    parsed = parser.parse_args(args)

    # load the config file as a global variable
    onlinejudge_verify.config.set_config_path(pathlib.Path(parsed.config_file))

    if getattr(parsed, 'jobs', None) is not None:
        # 先に並列で読み込みしておく
        onlinejudge_verify.marker.get_verification_marker(jobs=parsed.jobs)

    if parsed.subcommand == 'all':
        _delete_gitignore()
        generate_gitignore()
        summary = subcommand_run(paths=[], timeout=parsed.timeout, tle=parsed.tle, jobs=parsed.jobs)
        subcommand_docs(jobs=parsed.jobs)
        summary.show()
        if not summary.succeeded():
            sys.exit(1)

    elif parsed.subcommand == 'run':
        _delete_gitignore()
        generate_gitignore()
        summary = subcommand_run(paths=parsed.path, timeout=parsed.timeout, tle=parsed.tle, jobs=parsed.jobs)
        summary.show()
        if not summary.succeeded():
            sys.exit(1)

    elif parsed.subcommand == 'docs':
        _delete_gitignore()
        generate_gitignore()
        subcommand_docs(jobs=parsed.jobs)

    elif parsed.subcommand == 'stats':
        subcommand_stats(jobs=parsed.jobs)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
