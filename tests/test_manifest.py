"""The manifest is what makes this integration replace the built-in one."""

import json
from pathlib import Path

MANIFEST = Path(__file__).parent.parent / "custom_components/channels/manifest.json"


def test_manifest_overrides_the_built_in_domain_and_is_discoverable():
    manifest = json.loads(MANIFEST.read_text())

    assert manifest["domain"] == "channels"
    # Home Assistant refuses a custom integration without a version.
    assert manifest["version"]
    assert manifest["config_flow"] is True
    assert manifest["zeroconf"] == [
        "_channels_app._tcp.local.",
        "_channels_dvr._tcp.local.",
    ]
