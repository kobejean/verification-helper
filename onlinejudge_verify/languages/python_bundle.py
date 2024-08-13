import ast
import os
import sys
from typing import Set, Dict, List, Any, Sequence
from pathlib import Path

class PythonBundler:
    def __init__(self, include_paths: List[Path]):
        self.include_paths = [p.resolve() for p in include_paths]
        self.processed_files: Set[Path] = set()
        self.bundled_code: Dict[Path, str] = {}
        self.import_order: List[Path] = []

    def process_file(self, file_path: Path) -> None:
        if file_path in self.processed_files:
            return
        self.processed_files.add(file_path)
        with open(file_path, 'r') as file:
            content = file.read()
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) or isinstance(node, ast.ImportFrom):
                if isinstance(node, ast.ImportFrom) and node.level > 0:
                    # Handle relative imports
                    module = node.module or ''
                    parts = list(file_path.parent.parts)[:-node.level]
                    parts.extend(module.split('.'))
                    module_path = Path(*parts).with_suffix('.py')
                else:
                    # Handle absolute imports
                    module = node.names[0].name if isinstance(node, ast.Import) else node.module
                    module_path = self.find_module(module)
                if module_path:
                    self.process_file(module_path)
        # Remove import statements and store the modified code
        self.bundled_code[file_path] = self.remove_imports(tree, file_path)
        self.import_order.append(file_path)

    def find_module(self, module: str) -> Path:
        for path in self.include_paths:
            full_path = path / Path(*module.split('.')).with_suffix('.py')
            if full_path.exists():
                return full_path
        return None

    def remove_imports(self, tree: ast.AST, file_path: Path) -> str:
        class ImportRemover(ast.NodeTransformer):
            def __init__(self, bundler):
                self.bundler = bundler

            def visit_Import(self, node):
                for alias in node.names:
                    if self.bundler.find_module(alias.name):
                        return None
                return node

            def visit_ImportFrom(self, node):
                if node.level > 0:
                    # Handle relative imports
                    module = node.module or ''
                    parts = list(file_path.parent.parts)[:-node.level]
                    parts.extend(module.split('.'))
                    module_path = Path(*parts).with_suffix('.py')
                    if module_path in self.bundler.processed_files:
                        return None
                else:
                    if self.bundler.find_module(node.module):
                        return None
                return node

        # Remove import statements
        new_tree = ImportRemover(self).visit(tree)
        ast.fix_missing_locations(new_tree)
        # Generate the modified code
        return ast.unparse(new_tree)

    def get_relative_path(self, file_path: Path) -> str:
        file_path = file_path.resolve()
        for include_path in self.include_paths:
            try:
                return file_path.relative_to(include_path)
            except ValueError:
                continue
        return file_path

    def update(self, path: Path) -> bytes:
        self.process_file(path)
        bundled_content = []
        for file_path in self.import_order:
            if file_path != path:
                relative_path = self.get_relative_path(file_path)
                bundled_content.append(f"# File: {relative_path}")
                bundled_content.append(self.bundled_code[file_path])
                bundled_content.append("\n")
        relative_path = self.get_relative_path(path)
        bundled_content.append(f"# File: {relative_path}")
        bundled_content.append(self.bundled_code[path])
        return "\n".join(bundled_content).encode()