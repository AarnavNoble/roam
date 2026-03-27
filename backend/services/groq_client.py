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
    pace_desc = {"relaxed": "fewer stops, more downtime", "moderate": "balanced pace", "packed": "maximize stops, tight schedule"}
    budget_desc = {"free": "only free activities", "budget": "cheap eats and free attractions", "mid": "mix of free and paid", "splurge": "premium experiences welcome"}
    style_desc = {"solo": "solo traveler", "couple": "traveling as a couple", "family": "family with kids", "group": "group of friends"}

    pace = pace_desc.get(trip.get("pace", "moderate"), "balanced pace")
    budget = budget_desc.get(trip.get("budget", "mid"), "mix of free and paid")
    style = style_desc.get(trip.get("style", "solo"), "solo traveler")
    notes = trip.get("notes", "")

    notes_instruction = ""
    if notes:
        notes_instruction = f"""
TRAVELER'S SPECIAL REQUESTS (enforce strictly):
"{notes}"
- Read these carefully before writing anything.
- If a request EXCLUDES a type of place (e.g. "I hate museums", "no bars", "avoid tourist traps"), OMIT those stops entirely — do not describe them, do not mention them.
- If a request INCLUDES a preference (e.g. "I love street food", "want hidden gems"), emphasize matching stops and briefly explain why they fit.
- Adjust each day's theme and summary to reflect what was kept, not what was removed.
"""

    return f"""You are a knowledgeable travel guide. Generate a detailed, natural day-by-day itinerary.

TRIP DETAILS:
- Destination: {trip['destination']}
- Duration: {trip['days']} days
- Transport: {trip['transport']}
- Goals: {', '.join(trip['goals'])}
- Pace: {pace}
- Budget: {budget}
- Traveler: {style}
{notes_instruction}
RELEVANT LOCAL KNOWLEDGE:
{rag_context}

OPTIMIZED ROUTE (filter and use what fits, in the order shown):
{json.dumps(itinerary, indent=2)}

INSTRUCTIONS:
- Apply the traveler's special requests first — drop any stops that conflict before writing
- Write a natural, engaging itinerary for the stops that remain
- For each stop include: what to do there, why it matches the traveler's goals, and a practical tip
- Include the estimated arrival time provided for each stop
- Mention travel time between stops where provided
- Keep each day focused and realistic given the pace setting
- Do not invent stops not in the list
- Output as structured JSON matching this format exactly:

{{
  "days": [
    {{
      "day": 1,
      "theme": "short theme for the day",
      "stops": [
        {{
          "name": "place name",
          "arrival_time": "HH:MM",
          "duration_min": 60,
          "description": "what to do and why",
          "tip": "practical tip",
          "lat": 0.0,
          "lon": 0.0,
          "category": "food"
        }}
      ],
      "summary": "one sentence day summary"
    }}
  ],
  "overview": "2-3 sentence trip overview"
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
