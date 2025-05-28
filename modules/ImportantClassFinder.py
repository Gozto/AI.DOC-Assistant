import logging
import os

from modules.CodeAnalyzer import CodeAnalyzer
from modules.RepositoryReader import RepositoryReader
from modules.TogetherAiAPIClient import TogetherAPIClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class ImportantClassFinder:
    """
    Identifikuje top N najdôležitejších tried v Python projekte podľa metrík CodeAnalyzer
    a umožňuje generovať ich detailnú AI analýzu s voliteľným zápisom do Markdown.
    """

    def __init__(self, together_client: TogetherAPIClient, reader: RepositoryReader, top_classes_count: int = 10,
                 max_tokens_per_chunk: int = 28000, max_output_tokens: int = 500,
                 token_counter=CodeAnalyzer.default_token_counter):
        """
        Inicializuje ImportantClassesFinder s AI klientom a readerom.
        """
        self.together_client = together_client
        self.top_classes_count = top_classes_count
        self.reader = reader
        self.max_tokens = max_tokens_per_chunk
        self.max_output = max_output_tokens
        self._count_tokens = token_counter

    def _get_allowed_output(self, prompt_text: str) -> int:
        """Vypočíta počet tokenov, ktoré AI môže vrátiť, na základe dĺžky promptu a nastavených limitov.

        Args:
            prompt_text (str): Text promptu odosielaného do AI.

        Returns:
            int: Maximálny počet tokenov, ktoré môže AI vrátiť (minimálne 0, maximálne self.max_output).
        """
        input_tokens = self._count_tokens(prompt_text)
        available = self.max_tokens - input_tokens - 1
        return max(0, min(self.max_output, available))

    def _calculate_importance_indexes_for_one_file(self, file_path: str, content: str, deps_map: dict[str, set[str]],
                                                   total_classes: int) -> list[dict]:
        classes_info = []
        if not file_path.endswith(".py"):
            return classes_info

        for class_name, code in CodeAnalyzer.extract_python_class_definitions(content):
            logging.info(f'Generujem index pre triedu: {class_name}')

            dependents = sum(1 for targets in deps_map.values() if class_name in targets)

            imp_index = CodeAnalyzer.calculate_importance_index_python(class_code=code, dependents=dependents,
                                                                       total_classes=total_classes)

            classes_info.append({"file": file_path, "name": class_name, "code": code, "importance": imp_index,
                                 "dependents": dependents})

        return classes_info

    def _generate_class_prompt(self, class_info) -> str:
        """
        Vygeneruje prompt pre analýzu triedy, vrátane nových metrík.
        """
        methods = class_info.get("methods_list", [])
        methods_str = "\n".join(f"- {m}" for m in methods) or "- žiadne"

        return f"""
Si expert v analýze softvérového kódu.
Analyzuj nasledujúcu triedu:

# {class_info['name']} (Index: {class_info['importance']})

**Súbor:** {class_info['file']}

### Metódy
{methods_str}

### Metriky
- Počet definovaných metód: {len(class_info.get('methods_list', []))}
- Počet tried, ktoré závisia od tejto triedy (in-degree): {class_info['dependents']}
(Hodnota 0 znamená, že žiadna iná trieda v projekte túto triedu nevyužíva, hoci samotná trieda môže závisieť 
na iných triedach.)
- Komplexita a veľkosť: (už započítaná v Indexe)

Na základe počtu metód a volaní metód, počtu tried ktoré na nej závisia uveď, prečo je táto trieda dôležitá.
Výstup musí presne dodržať túto šablónu:

# {class_info['name']}

## Popis
- Stručný popis hlavnej funkcionality triedy (1-2 vety)

## Použitie
```python
# Ukážkový kód základného použitia triedy
```

## Dôležitosť
- Vysvetlenie prečo je trieda dôležitá na základe:
  - Počtu metód
  - Komplexnosti kódu
  - Počtu tried, ktoré majú túto triedu vo svojich závislostiach (ak je 0, žiadna iná trieda ju nevyužíva, 
  hoci ona sama môže závisieť na iných triedach)


Celý výsledok musí byť v slovenskom jazyku a dodržať presne tento Markdown formát.
Maš zakázané používať iné formátovanie ako je toto a písať iné časti dokumentácie vrátane nadpisov a podnadpisov, ktoré
sa nenachádzajú v šablóne.
"""

    def _write_analysis_to_file(self, output_file, top_classes) -> None:
        """
        Zapíše analýzu do Markdown súboru.
        """
        with open(output_file, "w", encoding="utf-8") as f:
            for info in top_classes:
                sig = CodeAnalyzer.extract_class_signature_and_members(info['code'])
                info['methods_list'] = [m['name'] for m in sig.get('methods', [])]
                prompt = self._generate_class_prompt(info)
                max_tokens = self._get_allowed_output(prompt)
                logging.info(f"Generujem analýzu triedy {info['name']} do súboru.")
                result = self.together_client.get_ai_response(prompt, max_tokens=max_tokens, temperature=0.2)
                f.write(result + "\n\n")

    def find_important_classes(self) -> dict[str, dict]:
        """
        Vypočíta index dôležitosti všetkých tried (okrem testov) a vráti top N.
        """
        files_dict = self.reader.read_files()
        deps_map = CodeAnalyzer.get_class_dependencies(files_dict)
        total_classes = len(deps_map)

        classes_info = []
        for file_path, content in files_dict.items():
            lower = file_path.replace("\\", "/").lower()
            if (lower.startswith("tests/") or "/tests/" in lower or os.path.basename(lower).startswith(
                    "test_") or os.path.basename(lower).endswith("_test.py")):
                continue

            classes_info.extend(
                self._calculate_importance_indexes_for_one_file(file_path, content, deps_map, total_classes))

        classes_info.sort(key=lambda x: x['importance'], reverse=True)
        top = classes_info[:self.top_classes_count]

        return {info['name']: info for info in top}

    def find_and_write_important_classes(self, file_name: str = 'important_classes.md',
                                         output_dir: str = './generated_docs') -> dict[str, dict]:
        """
        Nájde najdôležitejších N tried a zapíše ich analýzu do súboru.
        """
        top = self.find_important_classes()

        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, file_name)
        self._write_analysis_to_file(output_file, list(top.values()))
        logging.info(f"Výsledok bol zapísaný do: {output_file}")

        return top
