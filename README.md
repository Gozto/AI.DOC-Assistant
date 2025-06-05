# AI.DOC-Assistant – Streamlit Web App

Tento projekt je súčasťou bakalárskej práce zameranej na **porozumenie softvérového kódu pomocou AI**.  
Ide o Streamlit webovú aplikáciu, ktorá:

- Klonuje verejné GitHub repozitáre
- Vykonáva **statickú analýzu kódu**
- Pomocou AI:
  - generuje podrobnú dokumentáciu (v slovenčine)
  - rozpoznáva architektúru projektu
  - vyberá najdôležitejšie triedy a vysvetľuje ich význam
  - kreslí UML diagramy vrátane závislostí medzi metódami

## 🔧 Spustenie
```bash
# 1. Klonuj tento repozitár
git clone https://github.com/tvoj_novy_repo/bp_streamlit_app.git
cd bp_streamlit_app

# 2. Nainštaluj závislosti
pip install -r requirements.txt

# 3. Spusti aplikáciu
streamlit run app.py

