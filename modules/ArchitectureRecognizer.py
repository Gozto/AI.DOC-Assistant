import json
import logging
import os

import yaml

from modules.CodeAnalyzer import CodeAnalyzer
from modules.RepositoryReader import RepositoryReader
from modules.TogetherAiAPIClient import TogetherAPIClient


class ArchitectureRecognizer:
    """
    Analýza a rozpoznanie architektonického vzoru projektu.

    Číta zdrojové súbory, vytvorí závislosti, získa heuristiku, moduly a potrebné
    informácie k zisteniu softwarovej architektúry. Tieto informácie potom
    pošle AI, ktorá odhadne softwarovú architektúru a napíše odôvodnenie.
    """

    def __init__(self, reader: RepositoryReader, ai_client: TogetherAPIClient, max_tokens_per_chunk: int = 28000,
                 max_output_tokens: int = 5000, token_counter=CodeAnalyzer.default_token_counter):
        """Inicializuje ArchitectureRecognizer.

        Args:
            reader: inštancia triedy, ktorá číta súbory z repozitára.
            ai_client: klient pre volanie AI API.
            max_tokens_per_chunk: maximálny počet tokenov pre vstup do AI.
            max_output_tokens: maximálny počet tokenov, ktoré AI vráti.
            token_counter: funkcia odhadujúca počet tokenov vo vstupe.
        """
        self.reader = reader
        self.ai = ai_client
        self.max_tokens = max_tokens_per_chunk
        self.max_output = max_output_tokens
        self._count_tokens = token_counter

    def get_project_modules(self, group_levels: int = 1, max_modules: int = 10000) -> list[str]:
        """
        Načíta súbory z repozitára a:
          - zoskupí každý adresár na prvé `group_levels` segmentov,
          - vyberie najvýznamnejších `max_modules` skupín (ostatné zmení na "other").
        Vracia len zoznam týchto modulov.
        """
        files = self.reader.read_files()

        raw_dirs = {os.path.dirname(p).replace('\\', '/') for p in files.keys()}

        def group_dir(d: str) -> str:
            parts = d.split('/')
            return '/'.join(parts[:group_levels]) or parts[0]

        grouped = {group_dir(d) for d in raw_dirs}

        class_deps = CodeAnalyzer.get_class_dependencies(files)
        class_to_group: dict[str, str] = {}
        for path, src in files.items():
            grp = group_dir(os.path.dirname(path).replace('\\', '/'))
            for cls in CodeAnalyzer.extract_classes_from_source(src):
                class_to_group[cls] = grp

        deps: dict[str, set[str]] = {}
        for src_cls, targets in class_deps.items():
            src_grp = class_to_group.get(src_cls)
            if not src_grp:
                continue
            for tgt_cls in targets:
                tgt_grp = class_to_group.get(tgt_cls)
                if tgt_grp and tgt_grp != src_grp:
                    deps.setdefault(src_grp, set()).add(tgt_grp)

        all_groups = set(grouped) | set(deps.keys())
        deg = {grp: len(deps.get(grp, [])) + sum(1 for vs in deps.values() if grp in vs) for grp in all_groups}

        if len(all_groups) > max_modules:
            keep = set(sorted(deg, key=deg.get, reverse=True)[:max_modules])
            modules = sorted(keep) + ["other"]
        else:
            modules = sorted(all_groups)

        return modules

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

    def _collect_heuristics(self, repo_root: str) -> dict:
        """
        Zistí z repozitára jednoduché signály:
          - entrypointy (manage.py, cli.py, __main__.py)
          - Dockerfile a docker-compose.yml
          - CI konfigurácie (GitHub Actions, Travis)
          - externé závislosti z requirements.txt alebo setup.cfg
        """
        h = {}

        # 1) Entrypoints
        entrypoints = []
        for fname in ("manage.py", "cli.py", "__main__.py"):
            if os.path.isfile(os.path.join(repo_root, fname)):
                entrypoints.append(fname)
        h['entrypoints'] = entrypoints

        # 2) Dockerove subory
        h['dockerfile'] = os.path.isfile(os.path.join(repo_root, "Dockerfile"))
        h['docker_compose'] = os.path.isfile(os.path.join(repo_root, "docker-compose.yml"))

        # 2a) Dockerfiles v podadresaroch
        dockerfiles = []
        for root, _, files in os.walk(repo_root):
            if "Dockerfile" in files:
                rel = os.path.relpath(root, repo_root)
                dockerfiles.append(rel or ".")

        h["dockerfiles"] = dockerfiles[:20]
        h["dockerfile_count"] = len(dockerfiles)

        compose_path = os.path.join(repo_root, "docker-compose.yml")
        if os.path.isfile(compose_path):
            with open(compose_path, encoding="utf-8") as f:
                docs = yaml.safe_load(f)
            services = docs.get("services", {})
            h["compose_services"] = list(services.keys())[:5]
            h["compose_service_count"] = len(services)

        # 3) CI (GitHub Actions, Travis CI)
        h['ci'] = {'github_actions': os.path.isdir(os.path.join(repo_root, ".github", "workflows")),
                   'travis': os.path.isfile(os.path.join(repo_root, ".travis.yml"))}

        # 4) Externé závislosti
        deps = set()

        # 4a) requirements.txt (root)
        req_txt = os.path.join(repo_root, "requirements.txt")
        if os.path.isfile(req_txt):
            with open(req_txt, encoding="utf-8") as f:
                for line in f:
                    pkg = line.strip().split("#", 1)[0].strip()
                    if pkg:
                        deps.add(pkg)

        # 4b) requirements-*.txt v root alebo v directory requirements/
        for fname in os.listdir(repo_root):
            if (fname.lower().startswith("requirements") and fname.lower().endswith(
                    ".txt") and fname != "requirements.txt"):
                path = os.path.join(repo_root, fname)
                if os.path.isfile(path):
                    with open(path, encoding="utf-8") as f:
                        for line in f:
                            pkg = line.strip().split("#", 1)[0].strip()
                            if pkg:
                                deps.add(pkg)

        # 4c) setup.cfg (install_requires)
        cfg_path = os.path.join(repo_root, "setup.cfg")
        if os.path.isfile(cfg_path):
            import configparser
            cfg = configparser.ConfigParser()
            cfg.read(cfg_path)
            if cfg.has_section("options") and cfg.has_option("options", "install_requires"):
                raw = cfg.get("options", "install_requires")
                for dep in raw.splitlines():
                    dep = dep.strip().rstrip(",")
                    if dep:
                        deps.add(dep)

        h['dependencies'] = sorted(deps)
        return h

    def _extract_pyproject_insights(self, toml_str: str) -> dict:
        """
        Pošle obsah pyproject.toml AI modelu a vráti kľúčové
        informácie relevantné pre rozpoznanie architektúry.
        Napr.:
          - entry_points (console_scripts, plugins…)
          - závislosti (web/cli/worker knižnice)
          - build-system (docker, packager…)
          - tool configurations (pytest, mypy, flake8)
        """

        prompt = f"""
You are an expert software architect.  
Below is the complete content of a pyproject.toml file for a Python project.  
Your task: extract and return in JSON format the following fields:

1. dependencies: a list of top-level package names (e.g. Django, Flask, Celery, etc.)  
2. entry_points: console scripts or plugin entry-points defined under [project.scripts] or [tool.poetry.scripts]  
3. build_system: the build-backend and requirements under [build-system]  
4. tool_configs: any tool-specific sections (pytest, flake8, mypy) with their key settings  
5. framework_hints: any indication of frameworks or patterns (e.g. 'Django', 'FastAPI', 'Celery', 'Click', 
'Flask-Plugin')  

Respond **only** with valid JSON that I can directly parse in python with json5.parse, for example:

{{
  "dependencies": ["Django>=3.2", "psycopg2", "..."],
  "entry_points": {{"cli": "mypkg.cli:main", "plugins": ["mypkg.ext:plugin"] }},
  "build_system": {{ "requires": [...], "backend": "..." }},
  "tool_configs": {{ "pytest": {{...}}, "mypy": {{...}} }},
  "framework_hints": ["MVC", "Plugin-based", "CLI"]
}}

Here is the file:
```toml
{toml_str}
"""
        raw = self.ai.get_ai_response(prompt, temperature=0.0, max_tokens=10000)
        try:
            insights = self.ai.trim_reponse_to_fit_json(raw)
        except ValueError:
            logging.warning("Neplatný JSON z pyproject-insights, vraciam prázdny dict.")
            return {}

        return insights or {}

    def recognize_architecture_from_metadata(self, repo_root: str, group_levels: int = 8, max_modules: int = 300,
                                             temperature: float = 0.1) -> dict:
        """
        Zozbiera:
          - zoskupené moduly (get_project_modules),
          - jednoduché heuristiky (_collect_heuristics),
          - detailné insighty z pyproject.toml (_extract_pyproject_insights),
        poskladá jednotný prompt a pošle ho AI, aby identifikovala architektúru a zdôvodnila ju.

        Returns:
            dict: {
              "architecture": "...",
              "justification": "..."
            }
        """
        modules = self.get_project_modules(group_levels=group_levels, max_modules=max_modules)
        heuristics = self._collect_heuristics(repo_root)
        insights = {}
        pyproj_path = os.path.join(repo_root, "pyproject.toml")
        if os.path.isfile(pyproj_path):
            with open(pyproj_path, encoding="utf-8") as f:
                toml_str = f.read()
            insights = self._extract_pyproject_insights(toml_str)

        prompt = f"""
You are an expert software architect. Based on the following information about a Python repository, identify its 
overall architectural pattern and provide a concise justification.

The GitHub repository is: {self.reader.repo_url}

1) Modules (grouped by first {group_levels} segment(s)):
{json.dumps(modules, indent=2)}

2) Heuristics (JSON object) with keys:
   - entrypoints (list of entrypoint scripts)
   - dockerfile (bool)
   - docker_compose (bool)
   - dockerfiles (list of up to 5 Dockerfile paths)
   - dockerfile_count (int)
   - compose_services (list of up to 5 service names)
   - compose_service_count (int)
   - ci.github_actions (bool)
   - ci.travis (bool)
   - dependencies (list of top-level package names)

{json.dumps(heuristics, indent=2)}

3) Pyproject.toml insights (JSON with keys):
   - dependencies: list of declared dependencies
   - entry_points: console scripts and plugin entry-points
   - build_system: build backend and requirements
   - tool_configs: settings for pytest, mypy, flake8, etc.
   - framework_hints: detected framework or pattern hints

{json.dumps(insights, indent=2)}

Use these guidelines to map signals to architecture patterns (analyze in this order):

1. **Microservices** (multiple independently deployable units):
   - Strong indicators: 
     - dockerfile_count > 1 + compose_service_count > 1
     - modules named after business capabilities (e.g. 'payment_service', 'auth_service')
     - compose_services with cross-dependencies (like API gateways, service discovery)
     - dependencies like 'nameko', 'fastapi', 'grpc'

2. **Event-Driven Architecture** (asynchronous message flows):
   - Strong indicators:
     - dependencies: 'celery', 'kafka', 'rabbitmq', 'pika'
     - modules: 'events', 'messages', 'consumers', 'producers', 'tasks'
     - heuristics.entrypoints includes worker scripts
     - docker-compose contains message brokers (redis, rabbitmq)

3. **Plugin System/Microkernel** (extensible core):
   - Strong indicators:
     - pyproject.toml entry_points defining plugins
     - modules: 'plugins', 'extensions', 'core' + many small modules
     - dependencies: 'pluggy', 'importlib', 'stevedore'

4. **Hexagonal Architecture** (ports & adapters):
   - Strong indicators:
     - modules: 'adapters', 'ports', 'domain', 'application'
     - framework_hints mentioning 'clean architecture'
     - dependencies separated into 'core' and 'infrastructure'

5. **Layered (N-Tier/MVC)** (strict hierarchy):
   - Strong indicators:
     - modules: 'controllers', 'services', 'repositories', 'models'
     - framework_hints: 'django', 'flask', 'spring'
     - entrypoints like 'manage.py' with migration commands

6. **CQRS** (command/query separation):
   - Strong indicators:
     - modules: 'commands', 'queries', 'events'
     - dependencies: 'cqrses', 'eventsourcing'
     - coexists with Event-Driven patterns

7. **Modular Monolith** (logically separated components):
   - Many modules grouped by features (e.g. 'billing', 'users', 'reports')
   - No strong signals for other patterns
   - Medium/high cohesion between feature modules

8. **Monolithic** (tightly coupled):
   - Few modules with generic names ('utils', 'helpers')
   - No architectural patterns detected
   - All logic in entrypoints like 'main.py'

9. **Client-Server Architecture
   - Strong indicators:
     • be careful when 
     • Separate modules: 'client'/'server' or 'api'/'frontend'
     • Dependencies: 
       - Server: 'flask', 'django', 'fastapi', 'grpc' 
       - Client: 'requests', 'aiohttp', 'grpc-client'
     • API contracts: OpenAPI specs, .proto files
     • Deployment: Different dockerfiles for client/server
     • Entrypoints: 'run_server.py' + 'client_cli.py'
   - Caution:
     • If the repository is primarily a framework or library (e.g. tiangolo/fastapi), 
     it may expose server‐side plumbing but isn’t itself a deployable client–server application. 
     In that case do not classify it as pure Client-Server as it is a framework or library.

CAUTION: CAUTION: If the project is primarily a library or framework (e.g. Pandas, FastAPI), label it as 
“Library/Framework” and do not classify it as a deployable Client–Server or Microservices application—rather, 
choose an appropriate internal architecture pattern (e.g. Layered, Modular Monolith) based on its module structure and 
dependencies. You must also choose its internal architecture pattern. (e.g. Library/Framework with Modular Monolith
internal architecture pattern)


Key decision principles:
- Prefer combinations when justified (e.g. "Modular Monolith with Event-Driven elements")
- Business domain modules > technical modules in pattern detection
- Framework usage (Django/Flask) suggests Layered unless strong Hexagonal signals
- Prioritize patterns with multiple confirming signals
- Docker/Compose alone ≠ Microservices - must have logical module separation

Analyze all evidence together. If multiple patterns apply, choose the most dominant based on:
1) Specificity of matching signals
2) Number of confirming heuristics
3) Logical consistency between components

Respond **only** with valid JSON and response architecture name MUST be in English and justification MUST be in 
slovak language:
{{
"architecture": "<pattern or combination>",
"justification": "<short explanation referencing the signals>"
}}
"""

        max_out = self._get_allowed_output(prompt)
        raw = self.ai.get_ai_response(prompt, temperature=temperature, max_tokens=max_out)

        try:
            result = self.ai.trim_reponse_to_fit_json(raw)
        except ValueError:
            logging.warning("AI odpoveď nebola platný JSON, vraciam hrubý text.")
            return {"architecture": None, "justification": raw.strip()}

        return {"architecture": result.get("architecture"), "justification": result.get("justification")}
