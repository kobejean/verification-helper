import ast
import os
import sys
from typing import Set, Dict, List, Tuple, Optional
from pathlib import Path
from collections import defaultdict

class PythonBundler:
    def __init__(self, include_paths: List[Path]):
        self.include_paths = [p.resolve() for p in include_paths]
        self.processed_files: Set[str] = set()
        self.bundled_code: Dict[Path, str] = {}
        self.top_level_imports_paths: Set[str] = set()  # Track top-level imports
        self.top_level_imports_modules: Set[str] = set()  # Track top-level imports
        self.top_level_imports_from: Dict[str,Set[str]] = defaultdict(set)  # Track top-level imports

    def process_file(self, file_path: Path, file_is_top_level: bool = True) -> None:
        if str(file_path) in self.processed_files:
            return
        self.processed_files.add(str(file_path))
        with open(file_path, 'r') as file:
            source = file.read()
        tree = ast.parse(source)
        self.bundled_code[file_path] = self.process_imports(tree, file_path, source, file_is_top_level)

    def import_file(self, file_path: Path, is_top_level: bool) -> str:
        is_duplicate = str(file_path) in self.top_level_imports_paths
        if not is_duplicate:
            if is_top_level:
                self.top_level_imports_paths.add(str(file_path))
            self.process_file(file_path, is_top_level)
            return self.bundled_code[file_path]
        return ""


    def process_imports(self, tree: ast.AST, file_path: Path, source: str, file_is_top_level: bool) -> str:
        class ImportProcessor(ast.NodeVisitor):
            def __init__(self, bundler):
                self.bundler = bundler
                self.imports: List[Tuple[int, int, str, str, bool]] = []  # Added boolean for is_duplicate

            def visit_Import(self, node):
                good = True
                for alias in node.names:
                    module_path = self.bundler.find_module(alias.name)
                    if module_path:
                        self.process_module(node, module_path, file_is_top_level)
                    else:
                        good = False
                if not good:
                    is_top_level = node.col_offset == 0 and file_is_top_level
                    new_names = set(alias.name for alias in node.names)
                    union = self.bundler.top_level_imports_modules | new_names
                    before = len(self.bundler.top_level_imports_modules)
                    after = len(union)
                    if before == after:
                        # skip
                        self.imports.append((node.lineno, node.col_offset, '', '', True))
                    if is_top_level:
                        self.bundler.top_level_imports_modules.update(new_names)

            def visit_ImportFrom(self, node):
                if node.level > 0:
                    raise NotImplementedError("Relative imports are not supported")
                else:
                    module_path = self.bundler.find_module(node.module)
                    if not module_path:
                        is_top_level = node.col_offset == 0
                        new_names = set(alias.name for alias in node.names)
                        union = self.bundler.top_level_imports_from[node.module] | new_names
                        before = len(self.bundler.top_level_imports_from[node.module])
                        after = len(union)
                        if before == after:
                            # skip
                            self.imports.append((node.lineno, node.col_offset, '', '', True))
                        if is_top_level:
                            self.bundler.top_level_imports_from[node.module].update(new_names)
                
                if module_path:
                    self.process_module(node, module_path, file_is_top_level, from_import=True, import_names=node.names)



            def process_module(self, node, module_path: Path, file_is_top_level: bool, from_import=False, import_names=None):
                is_duplicate = str(module_path) in self.bundler.top_level_imports_paths
                is_top_level = node.col_offset == 0 and file_is_top_level
                
                if not is_duplicate:
                    if module_path.is_dir():
                        raise NotImplementedError("Directory imports are not supported")
                    else:
                        imported_code = self.bundler.import_file(module_path, is_top_level)
                else:
                    imported_code = ""
                    
                if is_top_level and not is_duplicate:
                    self.bundler.top_level_imports_paths.add(str(module_path))
                
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
                imported_lines = imported_code.splitlines()
                if imported_lines:
                    relative_path = self.get_relative_path(Path(module_path))
                    # final_code.append(f"{indent}# BEGIN code from {relative_path}")
                    final_code.extend(f"{indent}{line}" for line in imported_lines)
                    # final_code.append(f"{indent}# END code from {relative_path}")
            
            last_import_line = lineno

        # Add any remaining lines after the last import
        final_code.extend(lines[last_import_line:])

        return '\n'.join(final_code)+'\n'

    def find_module(self, module: str) -> Optional[Path]:
        for path in self.include_paths:
            # Check if it's a file
            full_path = path / Path(*module.split('.')).with_suffix('.py')
            if full_path.exists():
                return full_path
        
        return None

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