import ast
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from plantuml import PlantUML

from modules.CodeAnalyzer import CodeAnalyzer
from modules.RepositoryReader import RepositoryReader
from modules.TogetherAiAPIClient import TogetherAPIClient


class UMLDiagramMaker:
    """
    Generuje UML class diagramy z Python kódu pomocou AI a PlantUML.
    """

    def __init__(self, together_client: TogetherAPIClient, reader: RepositoryReader,
                 output_dir: str = "../../uml_diagrams", plantuml_server: str = "http://www.plantuml.com/plantuml",
                 output_format: str = "svg", debug: bool = False, token_counter=CodeAnalyzer.default_token_counter,
                 max_tokens_per_prompt: int = 28000, max_output_tokens: int = 3500) -> None:

        """
        Inicializuje nástroj pre generovanie UML diagramov.
        """
        self.together_client = together_client
        self.output_dir = output_dir
        self.plantuml_server = plantuml_server.rstrip('/')
        self.output_format = output_format.lower()
        self.reader = reader
        self._count_tokens = token_counter
        self._max_tokens_per_prompt = max_tokens_per_prompt
        self._max_output_tokens = max_output_tokens
        # definicie tried a plant uml kodu
        self.class_definitions: dict[str, str] = {}
        # zdrojova class, cielova class, vztah, popis vztahu
        self.relationships: set[tuple[str, str, str, str]] = set()
        url = f"{self.plantuml_server}/{self.output_format}/"
        self.plantuml_client = PlantUML(url=url)

        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(logging.DEBUG if debug else logging.INFO)

        os.makedirs(self.output_dir, exist_ok=True)

        if self.output_format not in ['png', 'svg', 'txt']:
            raise ValueError(f"Invalid format {self.output_format}. Allowed: png, svg, txt")

        self.logger.info(f"Initialized UML diagram maker. Output directory: {self.output_dir}")

    def _get_allowed_output(self, prompt_text: str) -> int:
        """
        Vypočíta, koľko tokenov môže AI vrátiť, pričom berie do úvahy
        maximálny povolený súčet tokenov (prompt + odpoveď) a rezervu minimálneho
        počtu tokenov pre odpoveď.
        """
        used = self._count_tokens(prompt_text)
        available = self._max_tokens_per_prompt - used - 1
        return max(0, min(self._max_output_tokens, available))

    def generate_class_relationships_for_one_segment(self, class_code: str, files_dict: dict, class_name: str) -> dict:
        """
        Analyzuje fragment kódu špecifickej triedy a vráti vzťahy k iným triedam v projekte.
        """
        prompt = f"""
You are an expert in software analysis and UML diagram creation.
The following code is either an entire Python class or a fragment of a Python class. At the top, you have all the 
imports used by this class. Based on the following Python class code, identify all relationships that this class has 
with other classes in this project, ignoring any classes that come from external or well‑known libraries 
(for example, pandas.DataFrame).

Focus only on the following three types of relationships:
- **Inheritance:** If the class explicitly inherits from another class (e.g., `class SubClass(SuperClass):`), 
  create an entry where:
  - Key: Name of the PARENT class (SuperClass)
  - Value: "inheritance"
- **Association:** If the class uses or references other classes via its attributes, methods, or parameters 
  (without creating its own instances), label this relationship as "association"
- **Aggregation:** If the class creates and manages instances of other classes (for example, inside the constructor 
  or as part of its attributes), where those instances can exist independently, label this relationship as "aggregation"

For example:
If analyzing `class Dog(Animal):`, the dictionary should be {{"Animal": "inheritance"}}
If analyzing `class Car: def __init__(self, engine: Engine):`, the dictionary should be {{"Engine": "aggregation"}}

Return only the resulting dictionary in valid JSON format and nothing else.

Name of the current class is: {class_name}

Python Class Code:
{class_code}
"""
        max_attempts = 5
        attempts = 0
        while attempts < max_attempts:
            max_out = self._get_allowed_output(prompt)
            output = self.together_client.get_ai_response(prompt, max_tokens=max_out, temperature=0.0)
            try:
                result = self.together_client.trim_reponse_to_fit_json(output)
                project_classes = CodeAnalyzer.get_all_classes_set(files_dict)
                filtered_result = {key: value for key, value in result.items() if key in project_classes and
                                   key != class_name}
                return filtered_result
            except Exception as e:
                self.logger.error(f"Chyba pri parsovaní JSON: {e}. Opakujem požiadavku...")
                attempts += 1
        raise ValueError("Nepodarilo sa naparsovať validnú JSON odpoveď po niekoľkých pokusoch.")

    def generate_class_relationships_for_whole_class(self, class_code: str, class_name: str) -> dict:
        """
        Rozdelí kód celej triedy na segmenty, paralelne ich analyzuje a
        skombinuje zistené vzťahy tejto triedy k ostatným triedam v projekte.
        """
        segments = CodeAnalyzer.split_class_code_for_diagrams(class_code, max_lines=2500)
        files_dict = self.reader.read_files()

        PRIORITY = {"inheritance": 3, "aggregation": 2, "association": 1}

        def merge_rel(existing: str, new: str) -> str:
            return existing if PRIORITY[existing] >= PRIORITY[new] else new

        combined: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=min(5, len(segments))) as pool:
            futures = {pool.submit(self.generate_class_relationships_for_one_segment, seg, files_dict, class_name): seg
                       for seg in segments}

            for fut in as_completed(futures):
                try:
                    result = fut.result()
                    for other_cls, rel_type in result.items():
                        # ak este neni vztah k tej triede
                        if other_cls not in combined:
                            combined[other_cls] = rel_type
                        else:
                            # ak su 2 vztahy tak zlucim podla priority
                            combined[other_cls] = merge_rel(combined[other_cls], rel_type)
                except Exception as e:
                    self.logger.error(f"Segment failed: {e}")

        return combined

    def generate_plantuml_for_class_diagram(self, class_info: dict, relationships: dict) -> str:
        """
        Vygeneruje PlantUML kód pre danú triedu a jej vzťahy pomocou AI.
        """
        prompt = f"""
You are an expert in UML diagram generation. Based on the following information, generate a UML class diagram 
using PlantUML syntax. Generate only PlantUML code starting with @startuml and ending with @enduml, nothing else!
 Follow these rules STRICTLY:

1. **Class Structure:**
   - Start with `class {class_info['class_name']} {{ ... }}`
   - Attributes: List with `-` prefix
   - Methods: List with `+` prefix

2. **Relationships:**
   - Inheritance: Always use `ParentClass <|-- ChildClass` format
   - Association: Use `ClassA --> ClassB`
   - Aggregation: Use `ClassA o-- ClassB`
   - Add `: relationship_type` label after each relationship

3. **Exclude trivial or boilerplate methods:**  
   - **Do not** list simple getters (`getX`) or setters (`setX`).  
   - **Skip** dunder methods except `__init__` (e.g. `__str__`, `__repr__`, `__eq__`, etc.).  
   - **Omit** private helper methods (starting with a single underscore), unless they represent a real part of the 
   public API.

4. **Current Class: {class_info['class_name']}**
   - YOU ARE GENERATING DIAGRAM FOR THIS CLASS
   - All relationships must originate from or point to this class

Examples of CORRECT syntax:
- Inheritance: `BaseEstimator <|-- LogisticRegression : inheritance`
- Aggregation: `Car o-- Engine : aggregation`
- Association: `Student --> Course : association`

Now generate PlantUML code for:

### Class Info:
Name: {class_info['class_name']}
Attributes: {class_info['attributes'] or 'none'}
Methods: {class_info['methods'] or 'none'}

### Relationships to other classes:
{ {k: v for k, v in relationships.items()} }

IMPORTANT: Always double-check arrow directions for inheritance!
Generate only PlantUML code starting with @startuml and ending with @enduml, nothing else!
"""
        logging.info(f"Generujem PlantUML kód pre triedu {class_info['class_name']}")
        max_out = self._get_allowed_output(prompt)
        plantuml_code = self.together_client.get_ai_response(prompt=prompt, max_tokens=max_out, temperature=0.0)
        plantuml_code = plantuml_code.strip()
        return self.together_client.trim_plantuml_response(plantuml_code)

    def add_class_diagram(self, class_name: str, plantuml_code: str, rel_types: dict[str, str]) -> None:
        """
        Extrahuje definíciu triedy a vzťahy z vygenerovaného PlantUML kódu
        a uloží ich do interných štruktúr.

        1) Nájde blok definície aktuálnej triedy (`class X { ... }`) a uloží ho
           do `self.class_definitions`.
        2) Pre každý riadok PlantUML kódu, ktorý nie je definícia triedy alebo iného
           elementu, rozparsuje zdroj, cieľ a typ šípky.
        3) Určí typ vzťahu podľa `rel_types` alebo podľa tvaru šípky:
           - `<|--` -> "inheritance"
           - `o--` alebo `*--` -> "aggregation"
           - inak -> "association"
        4) Pridá štvoricu `(src, arrow, tgt, rel_type)` do `self.relationships`.
        """

        # 1)
        class_block = re.search(rf'(class\s+{class_name}\s*\{{[\s\S]*?\}})', plantuml_code)
        if class_block:
            self.class_definitions[class_name] = class_block.group(1)

        # 2)
        for line in plantuml_code.splitlines():
            text = line.strip()
            if not text or text.startswith(("class ", "interface ", "enum ")):
                continue

            m = re.match(r"^(\w+)\s+([^\s:]+)\s+(\w+)", text)
            if not m:
                continue
            src, arrow, tgt = m.groups()

            # 3)
            rel_type = rel_types.get(tgt)
            if not rel_type:
                if "<|--" in arrow:
                    rel_type = "inheritance"
                elif "o--" in arrow or "*--" in arrow:
                    rel_type = "aggregation"
                else:
                    rel_type = "association"

            # 4)
            self.relationships.add((src, arrow, tgt, rel_type))

    def build_full_diagram(self) -> str:
        """
        Poskladá celý PlantUML kód:
          @startuml
          [všetky definície tried]
          [všetky vzťahy]
          @enduml
        """
        parts = ["@startuml"]
        # 1) vsety definicie tried
        for cls_code in self.class_definitions.values():
            parts.append(cls_code)
        # 2) vsetky vztahy
        for src, arrow, tgt, rel_type in sorted(self.relationships):
            parts.append(f"{src} {arrow} {tgt} : {rel_type}")
        parts.append("@enduml")
        return "\n".join(parts)

    def generate_class_diagram_for_important_classes(self, important_classes: dict[str, dict]) -> str:
        """
        Pre každý záznam v important_classes (class_name -> class_info dict)
        vyextrahuje vzťahy aj členov, vygeneruje PUML kód, zostaví interné štruktúry
        """

        for class_name, info in important_classes.items():
            class_code = info.get("code")
            if not class_code:
                self.logger.warning(f"Chýba code pre {class_name}, preskočím.")
                continue

            # 1) vsetky vztahy a pretriedim ich nech zostanu len tie ktore smeruju na top triedy
            rels = self.generate_class_relationships_for_whole_class(class_code, class_name)
            rels = {other: rel_type for other, rel_type in rels.items() if other in important_classes}

            # 2) atributy triedy v diagrame nechcem
            signature = CodeAnalyzer.extract_class_signature_and_members(class_code)
            signature['attributes'] = []

            puml = self.generate_plantuml_for_class_diagram(signature, rels)
            self.add_class_diagram(class_name, puml, rels)

        full_puml = self.build_full_diagram()
        try:
            image_data = self.plantuml_client.processes(full_puml)
            out_path = os.path.join(self.output_dir, f"uml_class_diagram.{self.output_format}")
            with open(out_path, 'wb') as f:
                f.write(image_data)
            self.logger.info(f"Diagram generated to {out_path}")
            return full_puml
        except Exception as e:
            self.logger.error(f"Chyba pri generovaní UML obrázku: {e}")
            return full_puml

    def generate_method_dependency_diagram(self, target_file: str, class_name: str, method_name: str) -> str:
        """
        Vygeneruje PlantUML diagram, ktorý zobrazuje,
        ktoré metódy (z iných tried) volajú konkrétnu metódu
        class_name.method_name.
        """

        files = self.reader.read_files()
        callers: dict[str, set[str]] = {}

        target_code = files.get(target_file, "")
        try:
            _ = ast.parse(target_code)
        except SyntaxError:
            pass

        # 2) prejdem subory a zbieram callery
        for path, src in files.items():
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue

            for node in tree.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                caller_cls = node.name

                # vsetky self.xxx = ClassName(...) v celej triede
                init_attrs: set[str] = set()
                for sub in node.body:
                    if not isinstance(sub, ast.FunctionDef):
                        continue
                    for stmt in sub.body:
                        if not isinstance(stmt, ast.Assign):
                            continue
                        for tgt in stmt.targets:
                            if (isinstance(tgt, ast.Attribute) and isinstance(tgt.value,
                                                                              ast.Name) and tgt.value.id == "self" and isinstance(
                                stmt.value, ast.Call)):
                                # konstruktor priamo
                                if isinstance(stmt.value.func, ast.Name) and stmt.value.func.id == class_name:
                                    init_attrs.add(tgt.attr)
                                # cez factory volanie
                                if (isinstance(stmt.value.func,
                                               ast.Attribute) and class_name.lower() in stmt.value.func.attr.lower()):
                                    init_attrs.add(tgt.attr)

                param_objs: set[str] = set()
                for sub in node.body:
                    if not isinstance(sub, ast.FunctionDef):
                        continue
                    for arg in sub.args.args:
                        ann = getattr(arg, "annotation", None)
                        if isinstance(ann, ast.Name) and ann.id == class_name:
                            param_objs.add(arg.arg)

                for sub in node.body:
                    if not isinstance(sub, ast.FunctionDef):
                        continue
                    caller_m = sub.name
                    for call in ast.walk(sub):  # type: ignore
                        if (isinstance(call, ast.Call) and isinstance(call.func,
                                                                      ast.Attribute) and call.func.attr == method_name):
                            obj = call.func.value
                            # ked self.xxx.method_name()
                            if (isinstance(obj, ast.Attribute) and isinstance(obj.value,
                                                                              ast.Name) and obj.value.id == "self" and obj.attr in init_attrs):
                                callers.setdefault(caller_cls, set()).add(caller_m)
                            # ked param.method_name()
                            elif isinstance(obj, ast.Name) and obj.id in param_objs:
                                callers.setdefault(caller_cls, set()).add(caller_m)

        lines = ["@startuml", f"class {class_name} {{", f"    + {method_name}()", "}", ""]
        for caller_cls, methods in callers.items():
            lines.append(f"class {caller_cls} {{")
            for m in methods:
                lines.append(f"    + {m}()")
            lines.append("}")
            lines.append(f"{caller_cls} --> {class_name} : calls {method_name}()")
            lines.append("")
        lines.append("@enduml")
        puml = "\n".join(lines)

        try:
            image_data = self.plantuml_client.processes(puml)
            out_path = os.path.join(self.output_dir,
                                    f"method_dependency_{class_name}_{method_name}.{self.output_format}")
            with open(out_path, "wb") as f:
                f.write(image_data)
            self.logger.info(f"Method-dependency diagram generated to {out_path}")
        except Exception as e:
            self.logger.error(f"Chyba pri renderovaní method-dependency diagramu: {e}")

        return puml
