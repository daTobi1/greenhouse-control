def test_project_root_importable():
    """Stellt sicher, dass conftest.py die Projektwurzel in den sys.path legt."""
    import services  # noqa: F401


def test_numpy_available():
    import numpy as np
    assert np.zeros((2, 2)).sum() == 0
