import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app.tasks import extract_image_descriptions, derive_topic_from_text


def test_extract_image_descriptions_frequency():
    text = (
        "Un chat noir et un chien marron dans une ville. "
        "Le chat regarde la ville. La ville est belle."
    )
    result = extract_image_descriptions(text, max_items=3)
    assert result[:2] == ["ville", "chat"]
    assert len(result) == 3


def test_derive_topic_from_text():
    text = (
        "Un chat noir et un chien marron dans une ville. "
        "Le chat regarde la ville. La ville est belle."
    )
    topic, desc = derive_topic_from_text(text)
    assert topic == "ville chat noir"
    assert desc[:3] == ["ville", "chat", "noir"]
