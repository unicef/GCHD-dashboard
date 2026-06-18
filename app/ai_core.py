import json, os, re
from google import genai
from config import HAZARD_MAP, HAZARD_TOPICS, ADMIN_DATA

_client = None
_SYSTEM_PROMPT = None
_country_names = []


def initialize_ai(country_names=None):
    global _client, _country_names
    if country_names:
        _country_names = country_names
    key_path = os.path.join(os.path.dirname(__file__), "credentials", "gemini_api_key.txt")
    with open(key_path) as f:
        api_key = f.read().strip()
    _client = genai.Client(api_key=api_key)


def _build_system_prompt():
    hazards   = "\n".join(f"  - {k}" for k in HAZARD_MAP if k != "Pixel Based Hazard Score")
    topics    = "\n".join(f"  - {t}" for t in HAZARD_TOPICS)
    levels    = "\n".join(f'  - "{l}"' for l in ADMIN_DATA)
    countries = "\n".join(f"  - {c}" for c in _country_names)
    return f"""You are an AI assistant for the UNICEF Global Child Hazard Database.
You help users visualize hazard layers and retrieve child exposure statistics.

AVAILABLE HAZARD LAYERS (use exact names):
{hazards}

AVAILABLE HAZARD TOPICS (use exact names):
{topics}

AVAILABLE ADMIN LEVELS (use exact values):
{levels}

AVAILABLE COUNTRIES/TERRITORIES (you MUST use one of these exact names for country_name):
{countries}

AVAILABLE ACTIONS:
1. show_hazard_layer       — display a specific hazard map layer
   params: {{"hazard_name": "<exact>", "country_name": "<optional>"}}
2. show_topic_layer        — display a hazard topic exposure layer
   params: {{"topic_name": "<exact>", "country_name": "<optional>"}}
3. exposure_summary        — child exposure stats for ALL hazard topics in a country
   params: {{"country_name": "<name>", "admin_level": "<exact level or omit for country level>"}}
4. hazard_exposure         — child exposure stats for ONE specific hazard topic
   params: {{"topic_name": "<exact topic>", "country_name": "<name>", "admin_level": "<exact level or omit for country level>"}}
5. multi_hazard_exposure   — children exposed to MULTIPLE hazard topics simultaneously (overlap)
   params: {{"topic_names": ["<topic1>", "<topic2>", ...], "country_name": "<name>", "admin_level": "<exact level or omit for country level>"}}
6. zoom_to_country         — zoom map to a country without changing layers
   params: {{"country_name": "<name>"}}
7. reject                  — request is outside scope
   params: {{}}

Rules:
- Reject anything unrelated to hazard data, child exposure, or geographic navigation.
- country_name MUST be one of the exact names from AVAILABLE COUNTRIES/TERRITORIES above — never invent or translate a country name.
- admin_level defaults to "adm0 (Country)" when the user asks about a whole country and does not specify a sub-national level.
- For "how many children are exposed to X in Y?" → use hazard_exposure with the matching topic_name.
- For "how many children are exposed to X and Y in Z?" → use multi_hazard_exposure.
- Respond ONLY with valid JSON — no markdown fences, no extra text.

Response schema:
{{"action": "<action>", "params": {{...}}, "message": "<brief human-readable summary>"}}"""


def ask_gemini(user_query: str) -> dict:
    global _SYSTEM_PROMPT
    if _client is None:
        raise RuntimeError("AI not initialised — call initialize_ai() at startup.")
    if _SYSTEM_PROMPT is None:
        _SYSTEM_PROMPT = _build_system_prompt()
    prompt   = _SYSTEM_PROMPT + "\n\nUser query: " + user_query
    response = _client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
    text     = response.text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$",          "", text)
    return json.loads(text)
