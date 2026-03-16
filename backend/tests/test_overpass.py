from backend.services.overpass import _is_chain, _infer_category, POI


def test_chain_filter_english():
    assert _is_chain("Starbucks", {}) is True
    assert _is_chain("McDonald's", {}) is True
    assert _is_chain("subway", {}) is True


def test_chain_filter_by_brand_tag():
    assert _is_chain("スターバックス", {"brand": "Starbucks"}) is True
    assert _is_chain("マクドナルド", {"brand": "McDonald's"}) is True


def test_local_place_not_filtered():
    assert _is_chain("Ramen Ichiran", {}) is False
    assert _is_chain("Local Cafe", {}) is False


def test_infer_category_food():
    assert _infer_category({"amenity": "restaurant"}) == "food"
    assert _infer_category({"amenity": "cafe"}) == "food"


def test_infer_category_history():
    assert _infer_category({"historic": "monument"}) == "history"


def test_infer_category_nature():
    assert _infer_category({"leisure": "park"}) == "nature"


def test_infer_category_culture():
    assert _infer_category({"tourism": "museum"}) == "culture"


def test_poi_description():
    poi = POI(id=1, name="Ramen Shop", lat=35.68, lon=139.76, category="food",
              tags={"amenity": "restaurant", "cuisine": "ramen"})
    desc = poi.description
    assert "Ramen Shop" in desc
    assert "ramen" in desc


def test_poi_to_dict():
    poi = POI(id=1, name="Test", lat=35.68, lon=139.76, category="food", tags={})
    d = poi.to_dict()
    assert d["name"] == "Test"
    assert d["lat"] == 35.68
    assert d["category"] == "food"
