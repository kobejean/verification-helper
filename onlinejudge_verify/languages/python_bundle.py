import ast
import os
import sys
from typing import Set, Dict, List, Tuple, Optional
from pathlib import Path

class PythonBundler:
    def __init__(self, include_paths: List[Path]):
        self.include_paths = [p.resolve() for p in include_paths]
        self.processed_files: Set[Path] = set()
        self.bundled_code: Dict[Path, str] = {}
        self.import_order: List[Path] = []
        self.top_level_imports: Set[str] = set()  # Track top-level imports

    def process_file(self, file_path: Path) -> None:
        if file_path in self.processed_files:
            return
        self.processed_files.add(file_path)
        with open(file_path, 'r') as file:
            source = file.read()
        tree = ast.parse(source)
        self.bundled_code[file_path] = self.process_imports(tree, file_path, source)
        self.import_order.append(file_path)

    def process_imports(self, tree: ast.AST, file_path: Path, source: str) -> str:
        class ImportProcessor(ast.NodeVisitor):
            def __init__(self, bundler):
                self.bundler = bundler
                self.imports: List[Tuple[int, int, str, str, bool]] = []  # Added boolean for is_duplicate

            def visit_Import(self, node):
                for alias in node.names:
                    module_path = self.bundler.find_module(alias.name)
                    if module_path:
                        self.process_module(node, module_path)

            def visit_ImportFrom(self, node):
                if node.level > 0:
                    # Handle relative imports
                    module = node.module or ''
                    parts = list(file_path.parent.parts)[:-node.level]
                    parts.extend(module.split('.'))
                    module_path = Path(*parts)
                else:
                    module_path = self.bundler.find_module(node.module)
                
                if module_path:
                    self.process_module(node, module_path, from_import=True, import_names=node.names)

            def process_module(self, node, module_path: Path, from_import=False, import_names=None):
                is_duplicate = str(module_path) in self.bundler.top_level_imports
                if node.col_offset == 0 and not is_duplicate:
                    self.bundler.top_level_imports.add(str(module_path))
                
                if not is_duplicate:
                    if module_path.is_dir():
                        imported_code = self.bundler.process_package(module_path, from_import, import_names)
                    else:
                        self.bundler.process_file(module_path)
                        imported_code = self.bundler.bundled_code[module_path]
                else:
                    imported_code = ""
                
                self.imports.append((node.lineno, node.col_offset, str(module_path), imported_code, is_duplicate))

        processor = ImportProcessor(self)
        processor.visit(tree)
        
        # Sort imports by their position in the file
        processor.imports.sort(key=lambda x: (x[0], x[1]))

        # Process the source and insert imported code
        lines = source.splitlines()
        final_code = []
        last_import_line = 0

        for lineno, col_offset, module_path, imported_code, is_duplicate in processor.imports:
            # Add lines up to this import
            final_code.extend(lines[last_import_line:lineno-1])
            
            if not is_duplicate:
                # Calculate the indentation
                indent = ' ' * col_offset
                
                # Add the imported code with proper indentation
                relative_path = self.get_relative_path(Path(module_path))
                imported_lines = imported_code.splitlines()
                if imported_lines:
                    final_code.append(f"{indent}# BEGIN code from {relative_path}")
                    final_code.extend(f"{indent}{line}" for line in imported_lines)
                    final_code.append(f"{indent}# END code from {relative_path}")
            
            last_import_line = lineno

        # Add any remaining lines after the last import
        final_code.extend(lines[last_import_line:])

        return '\n'.join(final_code)

    def find_module(self, module: str) -> Optional[Path]:
        for path in self.include_paths:
            # Check if it's a file
            full_path = path / Path(*module.split('.')).with_suffix('.py')
            if full_path.exists():
                return full_path
            
            # Check if it's a directory (package)
            dir_path = path / Path(*module.split('.'))
            if dir_path.is_dir() and (dir_path / '__init__.py').exists():
                return dir_path
        
        return None

    def process_package(self, package_path: Path, from_import: bool, import_names: List[ast.alias]) -> str:
        package_code = []
        
        if from_import and import_names:
            # Process specific imports
            for alias in import_names:
                if alias.name == '*':
                    # Import all non-private members, skipping those already imported at top level
                    for item in package_path.iterdir():
                        if item.is_file() and item.suffix == '.py' and not item.name.startswith('_'):
                            if str(item) not in self.top_level_imports:
                                self.process_file(item)
                                package_code.append(self.bundled_code[item])
                else:
                    item_path = package_path / f"{alias.name}.py"
                    if item_path.exists() and str(item_path) not in self.top_level_imports:
                        self.process_file(item_path)
                        package_code.append(self.bundled_code[item_path])
        else:
            # Process all Python files in the package, skipping those already imported at top level
            for item in package_path.iterdir():
                if item.is_file() and item.suffix == '.py' and not item.name.startswith('_'):
                    if str(item) not in self.top_level_imports:
                        self.process_file(item)
                        package_code.append(self.bundled_code[item])
        
        return '\n'.join(package_code)

    def get_relative_path(self, file_path: Path) -> str:
        file_path = file_path.resolve()
        for include_path in self.include_paths:
            try:
                return str(file_path.relative_to(include_path))
            except ValueError:
                continue
        return str(file_path)

    def update(self, path: Path) -> bytes:
        self.process_file(path)
        return self.bundled_code[path].encode()