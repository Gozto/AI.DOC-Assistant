import logging
import os

from modules.CodeAnalyzer import CodeAnalyzer
from modules.TogetherAiAPIClient import TogetherAPIClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class TextDocumentationMaker:
    """
    Vytvára Markdown dokumentáciu pre Python kód pomocou AI.
    Rozdelí zdroj na bloky (triedy, funkcie alebo čistý kód),
    zvolí šablónu a vygeneruje dokumentáciu v slovenčine.
    """

    def __init__(self, groq_client: TogetherAPIClient, max_tokens=28000, max_output=23000,
                 token_counter=CodeAnalyzer.default_token_counter):
        """
        Inicializuje TextDocumentationMaker s odovzdaným Groq API klientom.
        """
        self.groq_client = groq_client
        self.max_tokens = max_tokens
        self.max_output = max_output
        self.token_counter = token_counter

    def _get_allowed_output(self, prompt_text: str) -> int:
        """
        Vypočíta počet tokenov, ktoré AI môže vrátiť.
        """
        input_tokens = self.token_counter(prompt_text)
        available = self.max_tokens - input_tokens - 1
        return max(0, min(self.max_output, available))

    def _generate_doc_prompt_with_no_classes_and_no_functions(self, code_block: str) -> str:
        return f"""
You are an expert in writing software documentation.
Analyze the following block of code, which does not contain any function or method definitions,
and create documentation for it.
Briefly describe what is happening in the code.
The documentation should be written in the Slovak language and formatted using Markdown.
Code:
{code_block}
"""

    def _generate_doc_prompt_with_no_classes_and_functions(self, code_block: str) -> str:
        return f"""
You are an expert in software documentation writing.
Analyze the following code and generate documentation ACCORDING TO THE EXACT SPECIFICATION. 
The OUTPUT MUST BE WRITTEN IN SLOVAK.

The code you received contains local methods/functions definitions (one or more methods/functions), create documentation
using this structure:
a) Start with the section: ## Method: [exact_method_name]
b) Within each method follow these subsections:
    ## 1. Úvod
    ## 2. Atribúty
    ## 3. Use Case príklady
    ## 4. Zaujímavosti
    ## 5. Záver

If there are multiple methods/functions, repeat this structure for each method/function, separating them with a 
line: ---.

Standard formatting:
- Attributes: ### attribute_name
- Lists use dashes (-)

### Expected output format for code with classes:

## Class Name
## 1. Úvod
- Brief description of the purpose and main functionality of the method
- Context of use within the system

## 2. Atribúty
#### AttributeName1
- Type
- Description
- Default value (if any)
- some insights about this attribute (how it works, what is it for,...)
- If there are no attributes, write "Tento kód nemá atribúty"

#### AttributeNameN...

## 4. Use Case príklady
#### Example
- Usage scenario
- Sample code
- Expected output
- If there are no use case examples, write "Tento kód nemá use case príklady"

## 5. Zaujímavosti
- Any observations or interesting notes you noticed about the code

## 6. Záver
- Summary of key features
- Recommendations for use
- Maintenance and extensibility notes

---

## Method Name 2 (if exists, repeat the above structure)


REQUIREMENTS:
1. Strictly follow the heading format
2. Number all sections, including within classes
3. Always include all sections 1–5 for each entity
4. Do not add any personal comments
5. The entire documentation MUST BE WRITTEN IN SLOVAK
6. The entire documentation MUST BE IN MARKDOWN
7. Leave 3 blank lines at the end of the documentation
8. Do NOT be very brief, write the documentation so the person reading it will understand function of each element

PROHIBITIONS:
1. Changing the order of sections
2. Combining multiple classes into one section
3. Omitting section numbering
4. Adding custom formatting
5. Writing in any language other than Slovak

### Code:
{code_block}
"""

    def _generate_documentation_prompt_with_classes_and_functions(self, code_block: str) -> str:
        return f"""
You are an expert in software documentation writing.
Analyze the following code and generate documentation ACCORDING TO THE EXACT SPECIFICATION. 
The OUTPUT MUST BE WRITTEN IN SLOVAK.

The code you received contains local class definitions (one or more classes), create documentation 
using this structure:
a) Start with the section: ## Class: [exact_class_name]
b) Within each class follow these subsections:
    ## 1. Úvod
    ## 2. Atribúty
    ## 3. Metódy
    ## 4. Use Case príklady
    ## 5. Zaujímavosti
    ## 6. Záver

If there are multiple classes, repeat this structure for each class, separating them with a line: ---

Standard formatting:
- Attributes: ### attribute_name
- Methods: ### method_name()
- Lists use dashes (-)

### Expected output format for code with classes:

## Class Name
## 1. Úvod
- Brief description of the purpose and main functionality of the class/code
- Context of use within the system

## 2. Atribúty
#### AttributeName1
- Type
- Description
- Default value (if any)
- some insights about this attribute (how it works, what is it for,...)
- If there are no attributes, write "Tento kód nemá atribúty"

#### AttributeNameN...

## 3. Metódy
#### MethodName1
- Description of functionality
- Parameters
- Return value
- Exceptions (if any)
- If there are no methods, write "Tento kód nemá metódy"
- some insights about this method (how it works, what it does,...)
- NEVER include the full method code

#### MethodNameN...

## 4. Use Case príklady
#### Example
- Usage scenario
- Sample code
- Expected output
- If there are no use case examples, write "Tento kód nemá use case príklady"

## 5. Zaujímavosti
- Any observations or interesting notes you noticed about the code

## 6. Záver
- Summary of key features
- Recommendations for use
- Maintenance and extensibility notes

---

## Class Name 2 (if exists, repeat the above structure)

REQUIREMENTS:
1. Strictly follow the heading format
2. Number all sections, including within classes
3. Always include all sections 1–6 for each entity
4. Do not add any personal comments
5. The entire documentation MUST BE WRITTEN IN SLOVAK
6. The entire documentation MUST BE IN MARKDOWN
7. Leave 3 blank lines at the end of the documentation
8. Do NOT be very brief, write the documentation so the person reading it will understand function of each element

PROHIBITIONS:
1. Changing the order of sections
2. Combining multiple classes into one section
3. Omitting section numbering
4. Adding custom formatting
5. Writing in any language other than Slovak

### Code:
{code_block}
"""

    def generate_documentation(self, code_block: str, class_definitions=True, method_definitions=True) -> str:
        """
        Vygeneruje dokumentáciu pre daný blok kódu.

        Podľa prítomnosti tried a/metód vyberie správny prompt, spočíta maximálny povolený počet tokenov
        pre odpoveď a zavolá AI klienta na získanie dokumentácie.
        """
        if class_definitions and method_definitions:
            prompt = self._generate_documentation_prompt_with_classes_and_functions(code_block)
        elif method_definitions and not class_definitions:
            prompt = self._generate_doc_prompt_with_no_classes_and_functions(code_block)
        else:
            prompt = self._generate_doc_prompt_with_no_classes_and_no_functions(code_block)

        max_out = self._get_allowed_output(prompt)

        try:
            response = self.groq_client.get_ai_response(prompt, max_tokens=max_out, temperature=0.0)
            return response
        except Exception as e:
            logging.error(f"Chyba pri volaní Groq API: {str(e)}")
            return f"Chyba pri volaní Groq API: {str(e)}"

    def process_file(self, file_path: str, content: str, output_dir: str) -> None:
        """
        Rozdelí súbor na menšie bloky a vygeneruje pre každý z nich dokumentáciu.

        Pomocou CodeAnalyzer rozdelí obsah súboru na bloky (triedy, funkcie alebo čistý kód).
        Ak je blokov viac, vytvorí preň podadresár. Pre každý blok následne zavolá
        process_documentation_for_one_block na vygenerovanie a uložení dokumentáciu.
        """
        logging.info(f"Spracovávam: {file_path}")
        blocks = CodeAnalyzer.split_code_generic(file_path, content, max_block_length=750)

        # ak je viac blokov vytvorim podadresar
        if len(blocks) > 1:
            target_folder = self.make_dir_for_muiltiple_blocks_doc(file_path, output_dir)
        else:
            target_folder = output_dir

        for i, (code_block, block_info) in enumerate(blocks):
            self.process_documentation_for_one_block(block_info, blocks, code_block, file_path, i, target_folder)

    def make_dir_for_muiltiple_blocks_doc(self, file_path: str, output_dir: str) -> str:
        """
       Vytvorí a vráti cestu k podadresáru pre dokumentáciu,
       ak sa pre daný súbor generuje viacero blokov dokumentácie.
       """
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        docs_folder = os.path.join(output_dir, f"{base_name}_docs")
        os.makedirs(docs_folder, exist_ok=True)
        target_folder = docs_folder
        return target_folder

    def process_documentation_for_one_block(self, block_info: dict, blocks: list[tuple], code_block: str,
                                            file_path: str, i: int, target_folder: str) -> None:
        """
        Spracuje jeden blok kódu a vygeneruje preň dokumentáciu.
        """

        function_details = []
        if block_info.get('functions'):
            for fn in block_info['functions']:
                function_details.append(str(fn))
        else:
            function_details.append("Žiadne funkcie")

        rel_path = os.path.relpath(file_path, start=target_folder)
        rel_path = rel_path.replace(os.sep, '/')
        context_md = f"""
# Kontext dokumentácie

## Dokumentácia pre súbor: [{rel_path}]({rel_path})

## Entitné informácie

| **Entita** | **Zoznam** |
|------------|-----------|
| **Triedy** | {", ".join(block_info['classes']) if block_info.get('classes') else "Žiadne triedy"} |
| **Funkcie** | {", ".join(function_details)} |

## Riadkové rozpätie

- **Začiatok:** {block_info.get('line_range', (0, 0))[0]}
- **Koniec:** {block_info.get('line_range', (0, 0))[1]}

---

# AI dokumentácia:
"""

        class_definitions = True if block_info.get('classes') else False
        method_definitions = True if block_info.get('functions') else False

        documentation_ai = self.generate_documentation(code_block, class_definitions=class_definitions,
                                                       method_definitions=method_definitions)
        documentation = context_md + documentation_ai

        # ak je viac blokov do nazvu suboru pridam priponu _part<i+1>
        suffix = self.make_suffix(blocks, i)
        doc_file = os.path.join(target_folder, f"{os.path.basename(file_path)}_doc{suffix}.md")
        self.write_doc_to_file(doc_file, documentation)
        logging.info(f"Dokumentácia uložená: {doc_file}")

    def make_suffix(self, blocks: list[tuple], i: int) -> str:
        """
        Vytvorí príponu pre názov súboru dokumentácie, ak bolo blokov viacero.
        """
        if len(blocks) > 1:
            suffix = f"_part{i + 1}"
        else:
            suffix = ""
        return suffix

    def write_doc_to_file(self, doc_file: str, documentation: str) -> None:
        """
        Zapíše vygenerovanú dokumentáciu do súboru.
        """
        with open(doc_file, "w", encoding="utf-8") as f:
            f.write(documentation)

    def make_text_documentation(self, files: dict[str, str], output_dir: str) -> None:
        """
        Prejde zoznam súborov a vygeneruje dokumentáciu pre každý z nich.
        """
        os.makedirs(output_dir, exist_ok=True)
        for file_path, content in files.items():
            self.process_file(file_path, content, output_dir)

    def generate_readme(self, files: dict[str, str], output_dir: str, repo_root: str, readme_name: str = "README.md") \
            -> None:
        """
        Vygeneruje README.md pre celý projekt na základe:
         - popisu z pyproject.toml (ak existuje),
         - súborov a štruktúry,
         - kódových metrík,
         - dependencies z requirements.txt,
         - entrypoint skriptov,
         - license súboru.
        """
        os.makedirs(output_dir, exist_ok=True)

        # 1) pyproject.toml
        pyproject_content = ""
        pyproject_path = os.path.join(repo_root, "pyproject.toml")
        if os.path.isfile(pyproject_path):
            with open(pyproject_path, encoding="utf-8") as f:
                pyproject_content = f.read()

        # 2) subory
        file_list = "\n".join(f"- `{os.path.relpath(p, repo_root)}`" for p in sorted(files))

        # 3) kodove metriky
        total_files = len(files)
        total_lines = sum(content.count("\n") + 1 for content in files.values())
        total_classes = sum(len(CodeAnalyzer.extract_classes_from_source(c)) for c in files.values())
        metrics = (
            f"- Python súborov: **{total_files}**\n"
            f"- Riadkov kódu: **{total_lines}**\n"
            f"- Tried: **{total_classes}**\n"
        )

        # 4) entrypointy
        entrypoints = []
        for fname in ("__main__.py", "cli.py", "manage.py"):
            if os.path.isfile(os.path.join(repo_root, fname)):
                entrypoints.append(fname)
        entry_section = ", ".join(entrypoints) or "Žiadne entrypoint skripty"

        # 5) license
        lic = "Žiadny LICENSE súbor"
        for f in ("LICENSE", "LICENSE.txt"):
            path = os.path.join(repo_root, f)
            if os.path.isfile(path):
                lic = open(path, encoding="utf-8").read().splitlines()[0]
                break

        prompt = f"""
You are an AI assistant specialized in writing clear, user-friendly README files for Python projects.
Using the information below, generate a well-structured README.md in Slovak, formatted in Markdown. 
Make it easy to read and give a concise overview of what the project is, what is it used for and how to start with it.

## pyproject.toml content:
{pyproject_content or 'No pyproject.toml found.'}

## File structure
{file_list}

## Code metrics
{metrics}

## Entrypoint scripts
{entry_section}

## License
{lic}
"""

        max_out = self._get_allowed_output(prompt)
        readme = self.groq_client.get_ai_response(prompt, max_tokens=max_out, temperature=0.7)

        target = os.path.join(output_dir, readme_name)
        with open(target, "w", encoding="utf-8") as f:
            f.write(readme)
        logging.info(f"README vygenerovaný: {target}")
