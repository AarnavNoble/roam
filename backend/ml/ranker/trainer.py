"""
Trains the POI ranker on synthetic data.

Synthetic data strategy:
- Generate (user_goal, POI_list) pairs programmatically
- Label relevance using rule-based heuristics (category match + semantic sim)
- Train LightGBM lambdarank on these labels
- Model generalizes because features are semantic (embedding-based)

Run: python -m backend.ml.ranker.trainer
"""

import numpy as np
from .features import extract_features
from .model import POIRanker

# Synthetic training scenarios: (goals, list of POIs with known relevance)
TRAINING_SCENARIOS = [
    {
        "goals": ["local food", "street food", "authentic cuisine"],
        "pois": [
            {"name": "Ramen Ichiran", "category": "food", "description": "ramen ichiran food restaurant japanese noodles", "tags": {"amenity": "restaurant", "cuisine": "ramen"}},
            {"name": "Tsukiji Outer Market", "category": "food", "description": "tsukiji outer market food seafood sushi fresh fish", "tags": {"amenity": "marketplace", "cuisine": "seafood"}},
            {"name": "Senso-ji Temple", "category": "history", "description": "senso-ji temple historic buddhist shrine tokyo asakusa", "tags": {"historic": "temple"}},
            {"name": "Shinjuku Gyoen", "category": "nature", "description": "shinjuku gyoen national park garden cherry blossom", "tags": {"leisure": "park"}},
            {"name": "Yakitori Alley", "category": "food", "description": "yakitori alley grilled chicken skewers izakaya street food", "tags": {"amenity": "restaurant", "cuisine": "yakitori"}},
        ],
        "relevance": [3, 4, 0, 0, 4],  # 0=irrelevant, 1=low, 2=medium, 3=high, 4=perfect
    },
    {
        "goals": ["history", "ancient ruins", "cultural heritage"],
        "pois": [
            {"name": "Colosseum", "category": "history", "description": "colosseum ancient roman amphitheatre historic ruins monument", "tags": {"historic": "ruins"}},
            {"name": "Roman Forum", "category": "history", "description": "roman forum ancient ruins historic site archaeological", "tags": {"historic": "archaeological_site"}},
            {"name": "Trastevere Restaurant", "category": "food", "description": "trastevere trattoria italian food pasta wine", "tags": {"amenity": "restaurant", "cuisine": "italian"}},
            {"name": "Villa Borghese", "category": "nature", "description": "villa borghese park green space garden rome", "tags": {"leisure": "park"}},
            {"name": "Vatican Museums", "category": "culture", "description": "vatican museums art gallery sistine chapel history culture", "tags": {"tourism": "museum"}},
        ],
        "relevance": [4, 4, 0, 1, 3],
    },
    {
        "goals": ["nightlife", "bars", "clubs", "party"],
        "pois": [
            {"name": "Fabric Nightclub", "category": "nightlife", "description": "fabric nightclub electronic music dancing bar london", "tags": {"amenity": "nightclub"}},
            {"name": "Sky Bar", "category": "nightlife", "description": "sky bar rooftop cocktails drinks nightlife view", "tags": {"amenity": "bar"}},
            {"name": "British Museum", "category": "culture", "description": "british museum history art culture exhibits", "tags": {"tourism": "museum"}},
            {"name": "Hyde Park", "category": "nature", "description": "hyde park green space nature walking outdoor", "tags": {"leisure": "park"}},
            {"name": "Soho Pub", "category": "nightlife", "description": "soho pub drinks beer craft ale nightlife", "tags": {"amenity": "pub"}},
        ],
        "relevance": [4, 3, 0, 0, 3],
    },
    {
        "goals": ["nature", "hiking", "outdoor", "parks"],
        "pois": [
            {"name": "Blue Mountains", "category": "nature", "description": "blue mountains national park hiking trails outdoor nature", "tags": {"leisure": "nature_reserve"}},
            {"name": "Bondi Beach", "category": "nature", "description": "bondi beach outdoor nature swimming surf coastal walk", "tags": {"natural": "beach"}},
            {"name": "Opera House Restaurant", "category": "food", "description": "opera house bar dining food drinks harbour", "tags": {"amenity": "restaurant"}},
            {"name": "Royal Botanic Garden", "category": "nature", "description": "royal botanic garden plants nature outdoor walking", "tags": {"leisure": "garden"}},
            {"name": "The Rocks Bar", "category": "nightlife", "description": "the rocks pub bar nightlife drinks sydney", "tags": {"amenity": "bar"}},
        ],
        "relevance": [4, 4, 0, 4, 0],
    },
    {
        "goals": ["art", "museums", "culture", "galleries"],
        "pois": [
            {"name": "Louvre Museum", "category": "culture", "description": "louvre museum art gallery mona lisa paris culture history", "tags": {"tourism": "museum"}},
            {"name": "Centre Pompidou", "category": "culture", "description": "centre pompidou modern art gallery contemporary culture", "tags": {"tourism": "gallery"}},
            {"name": "Eiffel Tower", "category": "culture", "description": "eiffel tower landmark paris tourism attraction", "tags": {"tourism": "attraction"}},
            {"name": "Le Marais Bistro", "category": "food", "description": "le marais bistro french food dining restaurant", "tags": {"amenity": "restaurant"}},
            {"name": "Musee d'Orsay", "category": "culture", "description": "musee d'orsay impressionist art museum culture", "tags": {"tourism": "museum"}},
        ],
        "relevance": [4, 4, 2, 0, 4],
    },
]


def generate_training_data():
    X_all = []
    y_all = []
    groups = []

    for scenario in TRAINING_SCENARIOS:
        goals = scenario["goals"]
        pois = scenario["pois"]
        relevance = scenario["relevance"]

        # add tags to descriptions if missing
        for poi in pois:
            if "tags" not in poi:
                poi["tags"] = {}

        features = extract_features(goals, pois)
        X_all.append(features)
        y_all.extend(relevance)
        groups.append(len(pois))

    X = np.vstack(X_all)
    y = np.array(y_all, dtype=np.float32)
    return X, y, groups


def train():
    print("Generating training data...")
    X, y, groups = generate_training_data()
    print(f"Training on {len(X)} samples across {len(groups)} queries")

    ranker = POIRanker()
    ranker.train(X, y, groups)
    ranker.save()
    print("Ranker training complete.")


if __name__ == "__main__":
    train()
