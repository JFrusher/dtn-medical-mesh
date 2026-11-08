import cbor2
import pytest

from endpoint.fhir import (
    Attachment,
    FhirError,
    Message,
    Patient,
    Referral,
    Result,
    Vital,
    decode,
    encode,
)

PATIENT = Patient("MUR-0091", "Neema Lyimo", 28, "female")
MSG = Message(
    text="Fever 3 days, RDT positive",
    vitals=(Vital("temperature", 39.2), Vital("spo2", 94)),
    attachments=(Attachment("wound.jpg", "image/jpeg", b"\xff\xd8" + bytes(5000)),),
    author="Nurse A. Mollel",
    patient=PATIENT,
    referral=Referral("34 weeks, BP 160/110, headache", "pre-eclampsia", "G2P1", "MgSO4 given"),
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


@pytest.mark.req("REQ-APP-02", "REQ-APP-03", "REQ-APP-04")
def test_staff_app_resources_round_trip() -> None:
    answer = Message(
        "Send her now", in_response_to="ab12", decision="accepted", author="Dr. N. Mushi"
    )
    result = Message("", in_response_to="ab12", patient=PATIENT, result=Result("Hb", "8.1 g/dL"))
    for m in (MSG, answer, result):
        assert decode(encode(m, "2027-01-01T10:00:00Z")) == m
    assert decode(encode(answer, "t")).author == "Dr. N. Mushi"
    assert MSG.summary().startswith("Referral: 34 weeks")


def test_patient_holds_only_the_four_identifiers() -> None:
    assert set(Patient.__dataclass_fields__) == {"local_id", "name", "age", "sex"}
    with pytest.raises(FhirError):
        Patient("X", "Y", 200, "female")
    with pytest.raises(FhirError):
        Patient("X", "Y", 30, "f")
    with pytest.raises(FhirError):
        Message("ok", decision="accepted")  # a decision must answer a referral


def test_unknown_vital_rejected_both_ways() -> None:
    with pytest.raises(FhirError):
        encode(Message("x", vitals=(Vital("mood", 3),)), "t")
