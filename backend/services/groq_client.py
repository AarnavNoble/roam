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
TRAVELER'S NOTES:
"{notes}"
- ONLY remove a stop if the notes contain an EXPLICIT exclusion like "I hate museums", "no bars", "avoid tourist traps".
- Positive preferences ("I want to hike", "love street food", "great photos") mean you should KEEP matching stops and describe why they fit — do NOT remove stops because you think they don't perfectly match.
- Mention places the traveler has already visited (like Big Ben, London Eye) so they know to move on quickly.
- Do NOT drop a stop just because it wasn't explicitly requested — the ML pipeline already selected the best matches.
"""

    transport_desc = "public transit" if trip.get("transport") == "transit" else "walking"

    return f"""You are a local expert crafting a personalized journey for someone in {city}.

TRAVELER PROFILE:
- Starting from: {start_location}, {city}
- Time available: {duration_hours} hours
- Getting around: {transport_desc}
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
- OUTPUT EVERY STOP in the route list above — do not remove, skip, or merge any stops under any circumstances
- The only exception: if notes contain an EXPLICIT exclusion like "no museums" or "hate bars", remove only those stops
- Positive preferences in notes ("want to hike", "love food") mean describe the matching stops enthusiastically — not a reason to drop other stops
- Write this as a flowing journey from {start_location} through {city}
- For each stop: what to do there, why it fits this specific traveler, one practical tip
- For outdoor/nature stops, describe the experience (views, the walk, fresh air) not just "it's a park"
- Include arrival times and walking/transit time between stops
- The overview should feel like a knowledgeable friend recommending their day, not a brochure
- Do not invent stops not in the list
- The JSON output must contain exactly the same number of stops as the route list above
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
