from types import SimpleNamespace
import pytest
from seamless_share.why_not.lookup import endpoint_lookup_state


@pytest.mark.parametrize("available", [False, True])
def test_live_forward_mapping_wins_over_automatic_observations(available):
    endpoint = SimpleNamespace(
        spec=SimpleNamespace(raw="test"),
        get_transformation_result=lambda tf: "recorded",
        get_irreproducible_records=lambda tf: [{"result": "observed"}],
        buffer_available=lambda result: available,
    )
    state = endpoint_lookup_state(endpoint, "tf")
    assert state.state == ("PRESENT_AS_HIT" if available else "PRESENT_RESULT_UNAVAILABLE")
    assert state.details["result_checksum"] == "recorded"
    assert state.details["row_count"] == 1


def test_manual_quarantine_is_irreproducible():
    endpoint = SimpleNamespace(
        spec=SimpleNamespace(raw="test"),
        get_transformation_result=lambda tf: None,
        get_irreproducible_records=lambda tf: [{"result": "observed"}],
    )
    assert endpoint_lookup_state(endpoint, "tf").state == "IRREPRODUCIBLE"
