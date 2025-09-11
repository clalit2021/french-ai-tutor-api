import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app.tasks import extract_image_descriptions


def test_extract_image_descriptions_frequency():
    text = (
        "Un chat noir et un chien marron dans une ville. "
        "Le chat regarde la ville. La ville est belle."
    )
    result = extract_image_descriptions(text, max_items=3)
    assert result[:2] == ["ville", "chat"]
    assert len(result) == 3
