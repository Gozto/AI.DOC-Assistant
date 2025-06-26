import logging
import os
import re

import json5
from together import Together

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class TogetherAPIClient:
    """
    Klient pre Together AI chat completions.

    Načíta API kľúč (z parametra alebo zo súboru), komunikuje so službou Together
    a poskytuje metódy na:
      - generovanie odpovedí na základe textových promptov,
      - odstraňovanie interných <think> blokov,
      - extrakciu validného JSON5 obsahu z odpovedí,
      - vysekávanie PlantUML blokov z textu.
    """

    def __init__(self, together_key_file: str = os.path.join(os.path.dirname(__file__), 'togetherai_key.txt'),
                 model: str = "arcee-ai/coder-large"):
        """
        Inicializuje Together AI klienta.
        Načíta API kľúč zo súboru a nastaví model pre volania Together AI.
        """
        with open(together_key_file, 'r', encoding="utf-8") as f:
            self.api_key = f.read().strip()

        self.model = model
        self.client = Together(api_key=self.api_key)
        logging.info("Together API klient inicializovaný s modelom: %s", self.model)

    def trim_reponse_to_fit_json(self, response: str) -> dict:
        """
        Extrahuje z odpovede časť, ktorá obsahuje validný JSON objekt, a pokúsi sa ju parsovať do slovníka.
        """
        start_index = response.find('{')
        end_index = response.rfind('}')
        if start_index == -1 or end_index == -1 or start_index >= end_index:
            raise ValueError("Neplatný formát JSON v odpovedi.")

        json_str = response[start_index:end_index + 1]
        try:
            parsed = json5.loads(json_str)
            return parsed
        except ValueError as e:
            raise ValueError(f"Chyba pri parsovaní JSON5: {e}")

    def trim_plantuml_response(self, response: str) -> str:
        """
        Nájde a vráti celý PlantUML kód medzi @startuml a @enduml (vrátane).
        Ak taký blok neexistuje, vráti celý text ako fallback.
        """
        start_index = response.lower().find("@startuml")
        end_index = response.lower().find("@enduml")

        if start_index == -1 or end_index == -1 or start_index >= end_index:
            logging.warning("PlantUML blok nebol nájdený v odpovedi. Vraciam celý text ako fallback.")
            return response.strip()

        return response[start_index:end_index + len("@enduml")].strip()

    def get_ai_response(self, prompt: str, max_tokens: int = 3000, temperature: float = 0.5) -> str:
        """
        Vygeneruje odpoveď od AI na základe zadaného promptu.
        Automaticky odstráni všetky <think> sekcie.
        """
        try:
            response = self.client.chat.completions.create(model=self.model,
                                                           messages=[{"role": "user", "content": prompt}],
                                                           temperature=temperature, max_tokens=max_tokens)

            if response.choices:
                raw_content = response.choices[0].message.content

                # odstranim vsetky <think>
                clean_content = re.sub(r'<think>.*?</think>', '', raw_content, flags=re.DOTALL)

                return clean_content.strip() if clean_content else "Prázdna odpoveď od AI"

            return "Žiadna odpoveď od Together AI."

        except Exception as e:
            return f"Chyba pri volaní Together AI: {str(e)}"
