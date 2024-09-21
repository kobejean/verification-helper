# Python Version: 3.x
import concurrent.futures
import functools
import pathlib
import platform
import sys
import textwrap
from logging import getLogger
from typing import Any, Dict, List, Sequence, Tuple
from pathlib import Path

import importlab.environment
import importlab.fs
import importlab.graph

from onlinejudge_verify.languages.models import Language, LanguageEnvironment
from onlinejudge_verify.languages.python_bundle import PythonBundler

logger = getLogger(__name__)


class PythonLanguageEnvironment(LanguageEnvironment):
    def compile(self, path: pathlib.Path, *, basedir: pathlib.Path, tempdir: pathlib.Path) -> None:
        code = textwrap.dedent(f"""\
            #!{sys.executable}
            \"\"\"This is a helper script to run the target Python code.

            We need this script to set PYTHONPATH portably. The env command, quoting something, etc. are not portable or difficult to implement.
            \"\"\"

            import os
            import sys

            # arguments
            path = {repr(str(path.resolve()))}
            basedir = {repr(str(basedir.resolve()))}

            # run {str(path)}
            env = dict(os.environ)
            if "PYTHONPATH" in env:
                env["PYTHONPATH"] = basedir + os.pathsep + env["PYTHONPATH"] 
            else:
                env["PYTHONPATH"] = basedir  # set `PYTHONPATH` to import files relative to the root directory
            os.execve(sys.executable, [sys.executable, path], env=env)  # use `os.execve` to avoid making an unnecessary parent process
        """)
        with open(tempdir / 'compiled.py', 'wb') as fh:
            fh.write(code.encode())

    def get_execute_command(self, path: pathlib.Path, *, basedir: pathlib.Path, tempdir: pathlib.Path) -> List[str]:
        return [sys.executable, str(tempdir / 'compiled.py')]

@functools.lru_cache(maxsize=None)
def _python_list_depending_files(path: pathlib.Path, basedir: pathlib.Path) -> List[pathlib.Path]:
    # compute the dependency graph of the `path`
    env = importlab.environment.Environment(
        importlab.fs.Path([importlab.fs.OSFileSystem(str(basedir.resolve()))]),
        (sys.version_info.major, sys.version_info.minor),
    )
    try:
        executor = concurrent.futures.ThreadPoolExecutor()
        future = executor.submit(importlab.graph.ImportGraph.create, env, [str(path)], trim=True)
        if platform.uname().system == 'Windows':
            timeout = 5.0  # 1.0 sec causes timeout on CI using Windows
        else:
            timeout = 1.0
        res_graph = future.result(timeout=timeout)
    except concurrent.futures.TimeoutError as e:
        raise RuntimeError(f"Failed to analyze the dependency graph (timeout): {path}") from e
    try:
        node_deps_pairs = res_graph.deps_list()  # type: List[Tuple[str, List[str]]]
    except Exception as e:
        raise RuntimeError(f"Failed to analyze the dependency graph (circular imports?): {path}") from e
    
    logger.debug('the dependency graph of %s: %s', str(path), node_deps_pairs)
    
    # collect Python files which are depended by the `path` and under `basedir`
    res_deps = []  # type: List[pathlib.Path]
    res_deps.append(path.resolve())
    
    for node_, deps_ in node_deps_pairs:
        deps = list(map(pathlib.Path, deps_))
        for dep in deps:
            # Check if the dependency is not a builtin module
            if not is_builtin_module(dep.stem):
                if basedir.resolve() in dep.resolve().parents and dep.name != "__init__.py":
                    res_deps.append(dep.resolve())
    
    return list(set(res_deps))

def is_builtin_module(module_name: str) -> bool:
    """Check if a module is a built-in module."""
    return module_name in sys.builtin_module_names or module_name in sys.modules

class PythonLanguage(Language):
    def list_dependencies(self, path: pathlib.Path, *, basedir: pathlib.Path) -> List[pathlib.Path]:
        return _python_list_depending_files(path.resolve(), basedir)

    def bundle(self, path: Path, *, basedir: Path, options: Dict[str, Any]) -> bytes:
        include_paths: List[Path] = options.get('include_paths', [])
        bundler = PythonBundler(include_paths=[basedir] + include_paths)
        return bundler.update(path)

    def is_verification_file(self, path: pathlib.Path, *, basedir: pathlib.Path) -> bool:
        return '.test.py' in path.name
    
    def is_library_file(self, path: pathlib.Path, *, basedir: pathlib.Path) -> bool:
        return path.name and path.name[0] != '_' and '.test.py' not in path.name and '.py' in path.name

    def list_environments(self, path: pathlib.Path, *, basedir: pathlib.Path) -> Sequence[PythonLanguageEnvironment]:
        # TODO add another environment (e.g. pypy)
        return [PythonLanguageEnvironment()]
