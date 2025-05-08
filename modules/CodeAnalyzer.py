import os
import ast
import logging
import textwrap


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


class CodeAnalyzer:
    """
    Pomocná trieda pre statickú analýzu Python kódu.

    Poskytuje metódy na:
      - delenie zdrojových súborov na menšie bloky (funkcie, triedy),
      - extrakciu tried, metód a importov,
      - výpočet metrik (počet metód, atribútov, cyklomatická komplexita),
      - zostavenie závislostí medzi triedami,
      - odhad počtu tokenov pre prácu s LLM.
    """

    @staticmethod
    def split_code_for_text_doc_python(source_code: str, max_block_length: int = 400) -> list[tuple[str, dict]]:
        """
        Rozdelí Python kód na bloky s maximálnou dĺžkou, pričom zachováva celistvosť tried a funkcií.

        Args:
            source_code: Zdrojový kód ako reťazec
            max_block_length: Maximálny počet riadkov v jednom bloku
        """
        try:
            source = textwrap.dedent(source_code)
            tree = ast.parse(source)
            lines = source_code.splitlines()
            protected_blocks = CodeAnalyzer._collect_protected_blocks(tree)

            return CodeAnalyzer._split_into_blocks(lines, protected_blocks, max_block_length)

        except Exception as e:
            logging.error(f"Chyba pri parsovaní Python kódu: {str(e)}")
            return [
                (source_code, {
                    'classes': [],
                    'functions': [],
                    'line_range': (0, len(source_code.splitlines()) - 1)
                })
            ]

    @staticmethod
    def _collect_protected_blocks(tree: ast.AST) -> list[tuple]:
        """
        Zberá všetky triedy a funkcie s ich rozsahmi.
        """
        protected = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                start = node.lineno - 1
                end = node.end_lineno - 1 if hasattr(node, 'end_lineno') else start
                protected.append((start, end, node))
        return protected

    @staticmethod
    def _split_into_blocks(lines: list[str], protected_blocks: list[tuple[int, int, ast.AST]], max_block_length: int)\
            -> list[tuple[str, dict]]:
        """
        Rozdelí riadky kódu do blokov, pričom rešpektuje hranice chránených blokov.
        """
        blocks = []
        current_block = []
        current_length = 0
        block_start_line = 0

        for line_num, line in enumerate(lines):
            current_block.append(line)
            current_length += 1

            in_protected = any(start <= line_num <= end for (start, end, _) in protected_blocks)

            if current_length >= max_block_length and not in_protected:
                block_info = CodeAnalyzer._extract_block_info(protected_blocks, block_start_line, line_num)
                blocks.append(("\n".join(current_block), block_info))
                current_block = []
                current_length = 0
                block_start_line = line_num + 1

        if current_block:
            block_info = CodeAnalyzer._extract_block_info(protected_blocks, block_start_line, len(lines) - 1)
            blocks.append(("\n".join(current_block), block_info))

        return blocks

    @staticmethod
    def _extract_block_info(protected_blocks: list[tuple[int, int, ast.AST]], start_line: int, end_line: int) -> dict:
        """
        Zistí, ktoré triedy a funkcie sa nachádzajú v danom rozsahu riadkov.
        """
        block_info = {
            'classes': [],
            'functions': [],
            'line_range': (start_line, end_line)
        }

        for start, end, node in protected_blocks:
            if start >= start_line and end <= end_line:
                if isinstance(node, ast.ClassDef):
                    block_info['classes'].append(node.name)
                elif isinstance(node, ast.FunctionDef):
                    block_info['functions'].append(node.name)

        return block_info

    @staticmethod
    def split_code_generic(file_path: str, source_code: str, max_block_length: int = 400) -> list:
        """
        Rozdelí kód na základe prípony súboru.
        Pre Python súbory extrahuje definície pomocou AST,
        """
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".py":
            return CodeAnalyzer.split_code_for_text_doc_python(source_code, max_block_length)
        else:
            return [source_code]

    @staticmethod
    def extract_python_class_definitions(source_code) -> list[tuple[str, str]]:
        """
        Extrahuje definície tried zo zdrojového kódu pomocou AST (Python).
        """
        source = textwrap.dedent(source_code)
        tree = ast.parse(source)
        class_defs = []
        lines = source_code.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                start_line = node.lineno - 1
                end_line = node.end_lineno
                code = "\n".join(lines[start_line:end_line])
                class_defs.append((node.name, code))
        return class_defs

    @staticmethod
    def _count_class_attributes_python(class_code):
        """
        Spočíta počet inštančných atribútov definovaných cez self v metódach triedy
        a atribútov definovaných priamo v tele triedy.
        """
        try:
            source = textwrap.dedent(class_code)
            tree = ast.parse(source)
        except Exception:
            return 0

        found_attributes = set()

        for class_node in tree.body:
            if isinstance(class_node, ast.ClassDef):

                # konrolujem atributy v tele triedy
                for item in class_node.body:
                    if isinstance(item, ast.Assign):
                        for target in item.targets:
                            if isinstance(target, ast.Name):
                                found_attributes.add(target.id)

                    # kontrolujem atributy v metodach
                    if isinstance(item, ast.FunctionDef):
                        for method_node in ast.walk(item):  # type: ignore
                            if isinstance(method_node, ast.Assign):
                                for target in method_node.targets:
                                    if isinstance(target, ast.Attribute):
                                        if isinstance(target.value, ast.Name) and target.value.id == 'self':
                                            found_attributes.add(target.attr)

        return len(found_attributes)

    @staticmethod
    def _compute_cyclomatic_complexity_python(class_code) -> int:
        """
        Jednoduchý výpočet cyklomatickej komplexnosti:
        Začneme s hodnotou 1 a pripočítame 1 za každé vetvenie (if, for, while, try, with).
        """
        try:
            source = textwrap.dedent(class_code)
            tree = ast.parse(source)
        except Exception:
            return 0
        complexity = 1
        for node in ast.walk(tree):
            if isinstance(node, (ast.If, ast.For, ast.While, ast.Try, ast.With)):
                complexity += 1
        return complexity

    @staticmethod
    def calculate_importance_index_python(class_code: str, dependents: int, total_classes: int) -> float:

        source = textwrap.dedent(class_code)
        tree = ast.parse(source)

        method_count = sum(isinstance(n, ast.FunctionDef) for n in ast.walk(tree))
        call_count = sum(isinstance(n, ast.Call) for n in ast.walk(tree))
        loc = len(class_code.splitlines())
        attr_count = CodeAnalyzer._count_class_attributes_python(class_code)
        complexity = CodeAnalyzer._compute_cyclomatic_complexity_python(class_code)
        norm_dependents = dependents / max(1, total_classes)
        norm_dependents = norm_dependents ** 1.5
        normalized_loc = loc / 10.0

        index = (
                0.25 * method_count +
                0.15 * call_count +
                0.10 * normalized_loc +
                0.10 * attr_count +
                0.10 * complexity +
                0.30 * norm_dependents
        )
        return round(index, 2)

    @staticmethod
    def get_all_classes_set(files_dict: dict) -> set[str]:
        """
        Prejde všetky Python súbory zo slovníka a vráti množinu názvov všetkých definovaných tried.
        """
        all_classes = set()
        for file_path, content in files_dict.items():
            try:
                class_defs = CodeAnalyzer.extract_python_class_definitions(content)
                for class_name, _ in class_defs:
                    all_classes.add(class_name)
            except Exception as e:
                logging.error(f"Chyba pri spracovaní súboru {file_path}: {e}")
        return all_classes

    @staticmethod
    def find_imports(source_code: str) -> str:
        """
        Prejde celý zdrojový kód a vráti všetky unikátne importovacie riadky.
        Hľadá riadky, ktoré začínajú 'import' alebo 'from', a vráti ich ako jeden blok.
        """
        lines = source_code.splitlines()
        imports = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                imports.append(line)
        unique_imports = list(dict.fromkeys(imports))
        return "\n".join(unique_imports)

    @staticmethod
    def split_class_code_for_diagrams(class_code: str, max_lines: int = 150) -> list[str]:
        """
        Rozdelí kód triedy na menšie segmenty s približne max_lines riadkami,
        pričom sa pokúsi zachovať celistvosť metód. Ak je kód kratší, ako max_lines,
        vráti sa jediný segment. Ku každému bloku vloží importy zo začiatku súboru pre ľahšiu identifikáciu
        rôznych tried a modulov z projektu. Tuto funkciu bud vyuzivat trieda UMLDiagramMaker
        """
        lines = class_code.splitlines()
        import_blocks = CodeAnalyzer.find_imports(class_code)
        if len(lines) <= max_lines:
            return [class_code]

        segments = []
        try:
            source = textwrap.dedent(class_code)
            tree = ast.parse(source)
        except Exception as e:
            logging.error(f"Chyba pri parsovaní kódu: {e}")
            for i in range(0, len(lines), max_lines):
                segments.append("\n".join(lines[i:i + max_lines]))
            return segments

        class_node = None
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                class_node = node
                break

        if class_node is None:
            for i in range(0, len(lines), max_lines):
                segments.append("\n".join(lines[i:i + max_lines]))
            return segments

        method_boundaries = []
        for node in class_node.body:
            if isinstance(node, ast.FunctionDef):
                start = node.lineno - 1  # 0-based index
                end = node.end_lineno if hasattr(node, 'end_lineno') else node.lineno
                method_boundaries.append((start, end))
        method_boundaries.sort(key=lambda x: x[0])

        if not method_boundaries:
            for i in range(0, len(lines), max_lines):
                segments.append("\n".join(lines[i:i + max_lines]))
            return segments

        current_segment = []
        current_lines_count = 0

        header_end = method_boundaries[0][0]
        header = "\n".join(lines[:header_end]).strip()
        if header:
            segment_lines = header.splitlines()
            current_segment.extend(segment_lines)
            current_lines_count += len(segment_lines)

        for start, end in method_boundaries:
            method_lines = lines[start:end]
            method_line_count = len(method_lines)

            if current_lines_count + method_line_count <= max_lines:
                current_segment.extend(method_lines)
                current_lines_count += method_line_count
            else:
                if current_segment:
                    segments.append("\n".join(current_segment))
                current_segment = method_lines.copy()
                current_lines_count = method_line_count

        last_method_end = method_boundaries[-1][1]
        if last_method_end < len(lines):
            tail_lines = lines[last_method_end:]
            if current_lines_count + len(tail_lines) <= max_lines:
                current_segment.extend(tail_lines)
            else:
                segments.append("\n".join(current_segment))
                current_segment = tail_lines
        if current_segment:
            segments.append("\n".join(current_segment))

        if import_blocks:
            segments = [f'{import_blocks}\n\n{segments[s]}' if import_blocks not in segments[s]
                        else f'{segments[s]}'
                        for s in range(len(segments))]
        return segments

    @staticmethod
    def extract_class_signature_and_members(class_code: str) -> dict:
        """
        Extrahuje názov triedy, atribúty a metódy z kódu jednej triedy.
        """
        try:
            source = textwrap.dedent(class_code)
            tree = ast.parse(source)
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    class_name = node.name
                    attributes = set()
                    methods = []

                    for sub in node.body:
                        if isinstance(sub, ast.FunctionDef):
                            method_name = sub.name
                            args = [arg.arg for arg in sub.args.args if arg.arg != 'self']
                            methods.append((method_name, args))

                            for stmt in ast.walk(sub):  # type: ignore
                                if isinstance(stmt, ast.Assign):
                                    for target in stmt.targets:
                                        if (isinstance(target, ast.Attribute) and
                                                isinstance(target.value, ast.Name) and target.value.id == 'self'):
                                            attributes.add(target.attr)

                    return {
                        "class_name": class_name,
                        "attributes": sorted(attributes),
                        "methods": [{"name": name, "args": args} for name, args in methods]
                    }

        except Exception as e:
            print(f"Chyba pri spracovaní AST: {e}")
        return {}

    @staticmethod
    def get_class_dependencies(files_dict: dict[str, str]) -> dict[str, set[str]]:
        """
        Pre každý .py súbor AST‑parsuje definície tried a zisťuje,
        na, ktoré iné triedy z projektu v ňom odkazuje.
        Deteguje:
          - dedenie (ClassDef.bases)
          - priame inštanciovanie Foo(…)
          - volania metód Foo.method(…)
          - type‑hinty (atribúty, parametre) x: Foo
          - návratové anotácie def f(…) -> Foo
          - isinstance(x, Foo)
          - raise Foo(…)
          - except Foo:
          - dekorátory na triedach aj metódach
        """
        all_classes = CodeAnalyzer.get_all_classes_set(files_dict)
        deps: dict[str, set[str]] = {cls: set() for cls in all_classes}

        for src in files_dict.values():
            try:
                source = textwrap.dedent(src)
                tree = ast.parse(source)
            except SyntaxError:
                continue

            # 1) Mapovanie aliasov importovaných tried
            import_alias = {}
            for node in tree.body:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        name = alias.name.split('.')[-1]
                        if name in all_classes:
                            import_alias[alias.asname or name] = name
                elif isinstance(node, ast.ImportFrom) and node.module:
                    for alias in node.names:
                        name = alias.name
                        if name in all_classes:
                            import_alias[alias.asname or name] = name

            # 2) najdeme vsetky triedy (aj vnorené)
            for class_node in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
                src_cls = class_node.name
                deps.setdefault(src_cls, set())

                # 2a) dedenie
                for base in class_node.bases:
                    if isinstance(base, ast.Name):
                        if base.id in all_classes and base.id != src_cls:
                            deps[src_cls].add(base.id)
                    elif isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
                        tgt = import_alias.get(base.value.id, base.value.id)
                        if tgt in all_classes and tgt != src_cls:
                            deps[src_cls].add(tgt)

                # 2b) dekoratory na triede
                for dec in class_node.decorator_list:
                    if isinstance(dec, ast.Name) and dec.id in all_classes and dec.id != src_cls:
                        deps[src_cls].add(dec.id)
                    elif isinstance(dec, ast.Attribute) and isinstance(dec.value, ast.Name):
                        tgt = import_alias.get(dec.value.id, dec.value.id)
                        if tgt in all_classes and tgt != src_cls:
                            deps[src_cls].add(tgt)

                # 3) prechadzame vnútro triedy
                for sub in ast.walk(class_node):  # type: ignore

                    # a) priame instancovanie Foo(...)
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                        tgt = import_alias.get(sub.func.id, sub.func.id)
                        if tgt in all_classes and tgt != src_cls:
                            deps[src_cls].add(tgt)

                    # b) volania metod Foo.method(...)
                    elif isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                        val = sub.func.value
                        if isinstance(val, ast.Name):
                            tgt = import_alias.get(val.id, val.id)
                            if tgt in all_classes and tgt != src_cls:
                                deps[src_cls].add(tgt)

                    # c) type‑hinty x: Foo
                    if hasattr(sub, "annotation") and isinstance(sub.annotation, ast.Name):
                        tgt = sub.annotation.id
                        if tgt in all_classes and tgt != src_cls:
                            deps[src_cls].add(tgt)

                    # d) navratove typy def f(...) -> Foo
                    if isinstance(sub, ast.FunctionDef) and sub.returns:
                        ret = sub.returns
                        if isinstance(ret, ast.Name) and ret.id in all_classes and ret.id != src_cls:
                            deps[src_cls].add(ret.id)

                    # e) isinstance(x, Foo) alebo isinstance(x, (Foo, Bar))
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "isinstance":
                        if len(sub.args) >= 2:
                            second = sub.args[1]
                            if isinstance(second, ast.Name) and second.id in all_classes and second.id != src_cls:
                                deps[src_cls].add(second.id)
                            elif isinstance(second, ast.Tuple):
                                for el in second.elts:
                                    if isinstance(el, ast.Name) and el.id in all_classes and el.id != src_cls:
                                        deps[src_cls].add(el.id)

                    # f) raise Foo(...)
                    if isinstance(sub, ast.Raise) and isinstance(sub.exc, ast.Call):
                        func = sub.exc.func
                        if isinstance(func, ast.Name):
                            tgt = import_alias.get(func.id, func.id)
                        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                            tgt = import_alias.get(func.value.id, func.value.id)
                        else:
                            tgt = None
                        if tgt in all_classes and tgt != src_cls:
                            deps[src_cls].add(tgt)

                    # g) except Foo:
                    if isinstance(sub, ast.ExceptHandler) and isinstance(sub.type, ast.Name):
                        if sub.type.id in all_classes and sub.type.id != src_cls:
                            deps[src_cls].add(sub.type.id)

                    # h) dekoratory na metodach
                    if isinstance(sub, ast.FunctionDef):
                        for dec in sub.decorator_list:
                            if isinstance(dec, ast.Name) and dec.id in all_classes and dec.id != src_cls:
                                deps[src_cls].add(dec.id)
                            elif isinstance(dec, ast.Attribute) and isinstance(dec.value, ast.Name):
                                tgt = import_alias.get(dec.value.id, dec.value.id)
                                if tgt in all_classes and tgt != src_cls:
                                    deps[src_cls].add(tgt)

        return deps

    @staticmethod
    def _get_full_attr_path(node: ast.AST) -> list[str]:
        """
        Z AST Attribute alebo Name získa úplnú cestu ako zoznam.
        Napr. `self.a.b.func` → ["self“, "a", "b", "func"].
        """
        if isinstance(node, ast.Name):
            return [node.id]
        elif isinstance(node, ast.Attribute):
            return CodeAnalyzer._get_full_attr_path(node.value) + [node.attr]  # type: ignore
        else:
            return []

    @staticmethod
    def extract_classes_from_source(src: str) -> set[str]:
        """
        Parse Python source and return the set of all class names defined in it.
        """
        try:
            source = textwrap.dedent(src)
            tree = ast.parse(source)
        except SyntaxError:
            return set()
        return {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}

    @staticmethod
    def default_token_counter(text: str) -> int:
        """
        Približný odhad počtu tokenov podľa počtu znakov.
        Môžete nahradiť funkciou z knižníc tiktoken alebo podobnej.
        """
        return max(1, len(text) // 4)
