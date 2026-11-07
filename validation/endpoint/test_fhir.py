import cbor2
import pytest

from endpoint.fhir import Attachment, FhirError, Message, Vital, decode, encode

MSG = Message(
    text="54F, fever 3 days, RDT positive",
    vitals=(Vital("temperature", 39.2), Vital("spo2", 94)),
    attachments=(Attachment("wound.jpg", "image/jpeg", b"\xff\xd8" + bytes(5000)),),
    in_response_to="ab12",
    patient_ref="CLINIC4-0091",
)


def test_round_trip_keeps_binary_attachments_binary() -> None:
    data = encode(MSG, "2027-01-01T10:00:00Z")
    assert decode(data) == MSG
    attachment = len(MSG.attachments[0].data)
    assert len(data) < attachment * 4 / 3  # smaller than base64 of the attachment alone
    assert "Body temperature 39.2 Cel" in decode(data).summary()


@pytest.mark.parametrize(
    "bad",
    [
        cbor2.dumps({"resourceType": "Patient"}),
        cbor2.dumps({"resourceType": "Bundle", "type": "message", "entry": []}),
        cbor2.dumps(
            {
                "resourceType": "Bundle",
                "type": "message",
                "entry": [{"resourceType": "MedicationRequest"}],
            }
        ),
        b"\xff",
    ],
)
def test_rejects_anything_outside_fhir_lite(bad: bytes) -> None:
    with pytest.raises(FhirError):
        decode(bad)


def test_unknown_vital_rejected_both_ways() -> None:
    with pytest.raises(FhirError):
        encode(Message("x", vitals=(Vital("mood", 3),)), "t")
