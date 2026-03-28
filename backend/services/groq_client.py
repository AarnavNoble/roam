"""
Groq LLM client for itinerary synthesis.
Takes structured itinerary data + RAG context and generates natural language output.
"""

import os
import json
from groq import Groq
from dotenv import load_dotenv

load_dotenv()
MODEL = "llama-3.3-70b-versatile"


def get_client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set in environment")
    return Groq(api_key=api_key)


def build_prompt(trip: dict, itinerary: list[dict], rag_context: str) -> str:
    pace_desc = {"relaxed": "fewer stops, more time to linger", "moderate": "balanced pace", "packed": "maximize stops, tight schedule"}
    budget_desc = {"free": "only free activities", "budget": "cheap eats and free attractions", "mid": "mix of free and paid", "splurge": "premium experiences welcome"}
    style_desc = {"solo": "solo traveler", "couple": "traveling as a couple", "family": "family with kids", "group": "group of friends"}
    dietary_desc = {"vegetarian": "vegetarian", "vegan": "vegan", "halal": "halal", "kosher": "kosher"}
    familiarity_desc = {"first_time": "first time visitor", "returning": "returning visitor who wants to go beyond the obvious"}

    pace = pace_desc.get(trip.get("pace", "moderate"), "balanced pace")
    budget = budget_desc.get(trip.get("budget", "mid"), "mix of free and paid")
    style = style_desc.get(trip.get("style", "solo"), "solo traveler")
    dietary = dietary_desc.get(trip.get("dietary", "none"), "")
    familiarity = familiarity_desc.get(trip.get("familiarity", "first_time"), "first time visitor")
    notes = trip.get("notes", "")
    start_location = trip.get("start_location", "")
    city = trip.get("city", "")
    duration_hours = trip.get("duration_hours", 6)

    notes_instruction = ""
    if notes:
        notes_instruction = f"""
TRAVELER'S SPECIAL REQUESTS (enforce strictly):
"{notes}"
- If a request EXCLUDES a type of place (e.g. "I hate museums", "no bars"), OMIT those stops entirely.
- If a request INCLUDES a preference (e.g. "I love street food"), emphasize matching stops and explain why they fit.
- Adjust each day's theme and summary to reflect what was kept.
"""

    return f"""You are a local expert crafting a personalized journey for someone in {city}.

TRAVELER PROFILE:
- Starting from: {start_location}, {city}
- Time available: {duration_hours} hours
- Getting around: {trip.get("transport", "walking")} and public transport
- Interests: {', '.join(trip.get("goals", []))}
- Pace: {pace}
- Budget: {budget}
- Traveling as: {style}
- Visitor type: {familiarity}
{f"- Dietary: {dietary}" if dietary else ""}
{notes_instruction}
LOCAL KNOWLEDGE:
{rag_context}

OPTIMIZED ROUTE (stops already ordered to minimize travel — follow this order):
{json.dumps(itinerary, indent=2)}

INSTRUCTIONS:
- Apply special requests first — drop conflicting stops before writing anything
- Write this as a flowing journey. The traveler is moving through {city} starting from {start_location}.
- For each stop: what to do there, why it fits this specific traveler, one practical tip
- Include arrival times and walking/transit time between stops
- The overview should feel like a knowledgeable friend recommending their day, not a brochure
- Do not invent stops not in the list
- Output as structured JSON matching this format exactly:

{{
  "days": [
    {{
      "day": 1,
      "theme": "short evocative theme for the day",
      "stops": [
        {{
          "name": "place name",
          "arrival_time": "HH:MM",
          "duration_min": 60,
          "description": "what to do and why it fits this traveler",
          "tip": "practical tip",
          "lat": 0.0,
          "lon": 0.0,
          "category": "food"
        }}
      ],
      "summary": "one sentence capturing the feel of the day"
    }}
  ],
  "overview": "2-3 sentences written like a friend describing a great day out"
}}"""


def generate_itinerary(trip: dict, itinerary: list[dict], rag_context: str) -> dict:
    """
    Call Groq to synthesize a natural language itinerary from structured data.
    Returns parsed JSON itinerary.
    """
    prompt = build_prompt(trip, itinerary, rag_context)

    response = get_client().chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content
    return json.loads(content)
