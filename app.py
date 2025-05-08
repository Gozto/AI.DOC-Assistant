import ast
import concurrent.futures
import textwrap
from pathlib import Path

import streamlit as st

from modules.ArchitectureRecognizer import ArchitectureRecognizer
from modules.CodeAnalyzer import CodeAnalyzer
from modules.ImportantClassFinder import ImportantClassesFinder
from modules.RepositoryReader import RepositoryReader
from modules.TextDocumentationMaker import TextDocumentationMaker
from modules.TogetherAiAPIClient import TogetherAPIClient
from modules.UMLDiagramMaker import UMLDiagramMaker

st.set_page_config(page_title="AI Code Assistant", layout="wide")

for key, default in [("repo_url", ""), ("clone_dir", "./cloned_repo"), ("repo_root", None), ("reader", None),
                     ("output_dir", "./output_dir"), ("architecture_result", None), ("top_classes", None),
                     ("plantuml_code", None), ]:
    if key not in st.session_state:
        st.session_state[key] = default

st.sidebar.title("📦 Repo Setup")

# 1) GitHub URL
repo_url = st.sidebar.text_input("GitHub repo URL", st.session_state.repo_url)
st.session_state.repo_url = repo_url

# 2) kde naklonovat repozitar
clone_dir = st.sidebar.text_input("Cesta pre klonovanie (dir)", st.session_state.clone_dir)
st.session_state.clone_dir = clone_dir

# 3) Vystup pre dokumentaciu
output_dir = st.sidebar.text_input("Cesta pre výstup dokumentácie", st.session_state.output_dir)
st.session_state.output_dir = output_dir

# tlacidlo na klonovanie / refresh
if st.sidebar.button("🔄 Clone / Refresh"):
    if not repo_url.strip():
        st.sidebar.error("Zadaj platnú GitHub URL.")
    elif not clone_dir.strip():
        st.sidebar.error("Zadaj cestu, kam klonovať.")
    else:
        reader = RepositoryReader(repo_url, clone_dir=clone_dir)
        with st.spinner(f"Klonujem do {clone_dir}…"):
            try:
                reader.clone_repository()
                st.session_state.reader = reader
                st.session_state.repo_root = reader.local_path
                st.session_state.architecture_result = None
                st.session_state.top_classes = None
                st.session_state.plantuml_code = None
                st.session_state.method_dep_puml = None

                st.cache_resource.clear()

                st.success(f"✔️ Naklonované do: {reader.local_path}")
            except RuntimeError as e:
                st.sidebar.error(str(e))

# ked nemame repo tak nejdem dalej
if not st.session_state.get("repo_root"):
    st.title("AI Code Assistant")
    st.write("Najprv zadaj URL a klonovací priečinok vľavo.")
    st.stop()


# inicializujem moje triedy
@st.cache_resource
def init_clients():
    reader = st.session_state.reader
    ai = TogetherAPIClient()
    doc_maker = TextDocumentationMaker(ai)
    arch_recognizer = ArchitectureRecognizer(reader=reader, ai_client=ai)
    important_finder = ImportantClassesFinder(together_client=ai, reader=reader)
    uml_maker = UMLDiagramMaker(together_client=ai, reader=reader,
                                output_dir=str(Path(st.session_state.output_dir) / "uml_diagrams"),
                                plantuml_server="http://www.plantuml.com/plantuml", output_format="svg")
    return doc_maker, arch_recognizer, important_finder, uml_maker


doc_maker, arch_recognizer, important_finder, uml_maker = init_clients()

# sidebar navigacia
page = st.sidebar.radio("⚙️ Vyber nástroj",
                        ["📄 Dokumentácia", "🔎 Dôležité triedy", "🏗️ Architektúra", "📊 UML Diagrams", ])

# ----------------------------------------------------------------
# Dokumentacia
# ----------------------------------------------------------------
if page == "📄 Dokumentácia":
    st.title("📝 Generátor dokumentácie (SK)")
    st.write("Vyber si .py súbor alebo vlož kód a klikni **Generovať**.")
    include_tests = st.checkbox(
        "Zahrnúť dokumentáciu pre testy (*.py v priečinkoch test alebo tests alebo test_*.py alebo *_test.py)",
        value=False)

    num_threads = st.number_input("Počet paralelných vlákien", min_value=1, max_value=10, value=4, step=1)

    # generovat pre vsetky subory
    if st.button("🗂️ Generovať dokumentáciu pre všetky .py súbory"):
        try:
            target = Path(output_dir).expanduser().resolve()
            target.mkdir(parents=True, exist_ok=True)
            files = st.session_state.reader.read_files()
            status_text = st.empty()
            progress_bar = st.progress(0)


            def should_skip(path: str) -> bool:
                parts = Path(path).parts
                name = Path(path).name
                return "test" in parts or "tests" in parts or name.startswith("test_") or name.endswith("_test.py")

            # pouzijem paralelizaciu pre rychlejsie generovanie
            def worker(args):
                file_path, content = args
                doc_maker.process_file(file_path, content, str(target))
                return file_path


            total = sum(1 for file_path in files if include_tests or not should_skip(file_path))

            with (st.spinner("Generujem dokumentáciu…"), concurrent.futures.ThreadPoolExecutor(
                    max_workers=num_threads) as pool):

                futures = []
                for fp in files.items():
                    file_path, content = fp
                    if not include_tests and should_skip(file_path):
                        continue
                    futures.append(pool.submit(worker, fp))

                for i, fut in enumerate(concurrent.futures.as_completed(futures), start=1):
                    current = fut.result()
                    status_text.text(f"Dokumentujem: `{current}`")
                    progress_bar.progress(i / total)

                status_text.text("")
                st.success(f"✔️ Dokumentácia uložená do: {target}")
        except Exception as e:
            st.error(f"Nepodarilo sa vygenerovať dokumentáciu: {e}")

    # generovat pre jeden subor
    files = st.session_state.reader.read_files()
    choice = st.selectbox("Vyber súbor z repozitára", ["— paste code manually —"] + sorted(files.keys()))
    if choice != "— paste code manually —":
        if st.button("🛠️ Generovať dokumentáciu pre vybraný súbor"):
            try:
                target = Path(output_dir).expanduser().resolve()
                target.mkdir(parents=True, exist_ok=True)
                with st.spinner(f"Generujem dokumentáciu pre {choice}…"):
                    doc_maker.process_file(choice, files[choice], str(target))
                st.success(f"✔️ Dokumentácia pre {choice} uložená do: {target}")
            except Exception as e:
                st.error(f"Nepodarilo sa vygenerovať dokumentáciu: {e}")
    else:
        code = st.text_area("alebo sem vlož kód:", height=300)
        if st.button("🛠️ Generovať dokumentáciu pre vložený kód"):
            if not code.strip():
                st.error("Žiadny kód na dokumentovanie.")
            else:
                with st.spinner("Generujem…"):
                    raw = textwrap.dedent(code)
                    block = CodeAnalyzer.split_code_for_text_doc_python(raw.strip(), max_block_length=1000)
                    # ked kod nepresiahne dlzku
                    if len(block) == 1:
                        code, code_info = block[0][0], block[0][1]
                        class_def = True if code_info.get('classes') else False
                        method_def = True if code_info.get('functions') else False
                        md = doc_maker.generate_documentation(code, class_definitions=class_def,
                                                              method_definitions=method_def)
                        st.markdown("---")
                        st.markdown(md, unsafe_allow_html=False)
                    else:
                        st.error('Kód ktorý si vložil je príliš dlhý.')

# ----------------------------------------------------------------
# Architektura
# ----------------------------------------------------------------
elif page == "🏗️ Architektúra":
    st.title("🏗️ Rozpoznanie architektúry")
    st.write("Analyzujem projekt a identifikujem architektonický vzor…")

    if st.button("🔍 Spustiť analýzu architektúry"):
        with st.spinner("Analýza…"):
            result = arch_recognizer.recognize_architecture_from_metadata(repo_root=st.session_state.repo_root)
            st.session_state.architecture_result = result

    # vysledky
    if st.session_state.architecture_result:
        arch = st.session_state.architecture_result.get("architecture")
        just = st.session_state.architecture_result.get("justification")
        st.subheader("Detekovaný vzor")
        st.write(f"**{arch}**")
        st.subheader("Zdôvodnenie")
        st.write(just)

# ----------------------------------------------------------------
# UML Diagrams
# ----------------------------------------------------------------
elif page == "📊 UML Diagrams":
    st.title("📊 UML Class Diagrams")
    st.write("Vygeneruj UML diagram pre top 10 najdôležitejších tried alebo dependency diagram pre konkrétnu metódu.")

    # button pre class diagram
    if st.button("▶️ Generovať class UML diagram"):
        # najdeme top triedy ak este nemame
        top = st.session_state.top_classes or important_finder.find_important_classes()
        st.session_state.top_classes = top

        with st.spinner("Generujem class UML diagram…"):
            plantuml_code = uml_maker.generate_class_diagram_for_important_classes(top)
            st.session_state.plantuml_code = plantuml_code

        # vysledok
        svg_path = Path(uml_maker.output_dir) / "uml_class_diagram.svg"
        if svg_path.exists():
            st.image(str(svg_path), caption="UML Class Diagram", use_container_width=True)
        else:
            st.warning("Nebolo možné nájsť vygenerovaný SVG pre class diagram.")

        st.subheader("PlantUML zdroj – Class Diagram")
        st.code(plantuml_code, line_numbers=True)

    st.markdown("---")

    # method dependency diagram
    st.subheader("📑 Dependency pre metódu")
    dep_file = st.selectbox("Vyber súbor s triedou", sorted(st.session_state.reader.read_files().keys()))
    dep_cls = st.text_input("Názov triedy", key="dep_cls")
    dep_meth = st.text_input("Názov metódy", key="dep_meth")
    if st.button("▶️ Generovať method-dependency diagram"):

        # overenie ci dana metoda a trieda existuju v subore
        src = st.session_state.reader.read_files().get(dep_file, "")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            st.error(f"Súbor {dep_file} sa nepodarilo parse-ovať.")
            st.stop()

        cls_node = None
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == dep_cls:
                cls_node = node
                break

        if cls_node is None:
            st.error(f"Trieda `{dep_cls}` sa v súbore `{dep_file}` nenašla.")
            st.stop()

        methods = {n.name for n in cls_node.body if isinstance(n, ast.FunctionDef)}
        if dep_meth not in methods:
            st.error(f"Metóda `{dep_meth}` sa v triede `{dep_cls}` nenašla. Dostupné metódy: {sorted(methods)}.")
            st.stop()

        with st.spinner("Analýza volaní..."):
            puml = uml_maker.generate_method_dependency_diagram(dep_file, dep_cls, dep_meth)
            st.session_state.method_dep_puml = puml

        img_path = Path(uml_maker.output_dir) / f"method_dependency_{dep_cls}_{dep_meth}.{uml_maker.output_format}"
        if img_path.exists():
            st.image(str(img_path), caption=f"Dependency diagram: {dep_cls}.{dep_meth} → callers",
                     use_container_width=True)
            st.subheader("PlantUML zdroj – Method Dependency")
            st.code(puml, line_numbers=True)
        else:
            st.warning("Obrázok sa nepodarilo vygenerovať pre method-dependency diagram.")

# ----------------------------------------------------------------
# Dolezite triedy
# ----------------------------------------------------------------
elif page == "🔎 Dôležité triedy":
    st.title("🔎 10 najdôležitejších tried")
    st.write("Analyzujem závislosti a AI metriky…")

    # 1) nazov .md suboru
    file_name = st.text_input("Názov výstupného Markdown súboru", value="important_classes.md")

    # 2) tlacidlo na start
    if st.button("▶️ Spustiť analýzu tried"):
        try:
            target_dir = Path(output_dir).expanduser().resolve()
            target_dir.mkdir(parents=True, exist_ok=True)

            with st.spinner("Analyzujem a generujem popisy tried…"):
                top = important_finder.find_and_write_important_classes(file_name, str(target_dir))

            md_path = target_dir / file_name
            st.success(f"✔️ Súbor vygenerovaný: `{md_path}`")

            # zobrazim vygenerovany .md aj na stranke
            md_text = md_path.read_text(encoding="utf-8")
            st.markdown("---")
            st.markdown(md_text)

        except Exception as e:
            st.error(f"Nepodarilo sa analyzovať triedy: {e}")
