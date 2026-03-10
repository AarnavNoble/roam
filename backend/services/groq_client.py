"""
Groq LLM client for itinerary synthesis.
Takes structured itinerary data + RAG context and generates natural language output.
"""

import os
import json
from groq import Groq

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
MODEL = "llama-3.3-70b-versatile"


def build_prompt(trip: dict, itinerary: list[dict], rag_context: str) -> str:
    return f"""You are a knowledgeable travel guide. Generate a detailed, natural day-by-day itinerary.

TRIP DETAILS:
- Destination: {trip['destination']}
- Duration: {trip['days']} days
- Transport: {trip['transport']}
- Goals: {', '.join(trip['goals'])}

RELEVANT LOCAL KNOWLEDGE:
{rag_context}

OPTIMIZED ROUTE (already ordered to minimize travel time):
{json.dumps(itinerary, indent=2)}

INSTRUCTIONS:
- Write a natural, engaging itinerary following the exact order of stops provided
- For each stop include: what to do there, why it matches the traveler's goals, and a practical tip
- Include the estimated arrival time provided for each stop
- Mention travel time between stops where provided
- Keep each day focused and realistic
- Do not add stops not in the list
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

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content
    return json.loads(content)
