import pytest
from app.nas_lifecycle import transition
def test_lifecycle_rejects_arbitrary_activation():
    with pytest.raises(ValueError): transition("DRAFT", "ACTIVE")
    assert transition("DRAFT", "CONNECTION_PENDING") == "CONNECTION_PENDING"
