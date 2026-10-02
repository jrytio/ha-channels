"""The library must stay usable without Home Assistant."""

from pathlib import Path

LIB = Path(__file__).parent.parent.parent / "custom_components/channels/lib"


def test_library_does_not_import_home_assistant():
    offenders = [
        path.name for path in LIB.glob("*.py") if "homeassistant" in path.read_text()
    ]

    assert offenders == []


def test_dvr_client_never_requests_the_unbounded_file_listing():
    source = (LIB / "dvr_client.py").read_text()

    assert '"/dvr/files' not in source
    assert "'/dvr/files" not in source
