from codemcp_bridge.doctor import collect_diagnostics


def test_phase0_configuration_is_valid():
    diagnostics = collect_diagnostics()

    assert diagnostics["status"] == "ok"
    assert diagnostics["configuration_errors"] == []
    assert diagnostics["codemcp"]["expected_release"] == "0.3.0"
    assert diagnostics["network_policy"]["model_egress"] == "deny"
    assert diagnostics["network_policy"]["remote_transport"] == "provider-selected"
    assert diagnostics["network_policy"]["recommended_remote_transport"] == "cloudflare"
    assert diagnostics["network_policy"]["secure_mcp_tunnel_optional"] is True
