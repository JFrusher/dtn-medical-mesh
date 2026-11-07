"""FHIR-lite clinical payloads (spec §6.4), encoded as CBOR so attachments stay binary.

A message is a FHIR-shaped Bundle of type "message" holding:
  Communication      the text (one per message), with inResponseTo for threads
  Observation        structured vitals
  DocumentReference  attachments (photos, PDFs, EKG traces, clips), data inline
Only these resource types and fields are accepted; anything else is rejected.
"""

from dataclasses import dataclass

import cbor2

VITALS = {  # code: (display, unit)
    "bp-systolic": ("Systolic blood pressure", "mmHg"),
    "bp-diastolic": ("Diastolic blood pressure", "mmHg"),
    "heart-rate": ("Heart rate", "/min"),
    "resp-rate": ("Respiratory rate", "/min"),
    "spo2": ("Oxygen saturation", "%"),
    "temperature": ("Body temperature", "Cel"),
    "glucose": ("Blood glucose", "mmol/L"),
}


class FhirError(ValueError):
    pass


@dataclass(frozen=True)
class Vital:
    code: str
    value: float


@dataclass(frozen=True)
class Attachment:
    title: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class Message:
    text: str
    vitals: tuple[Vital, ...] = ()
    attachments: tuple[Attachment, ...] = ()
    in_response_to: str | None = None  # bundle ID (hex) of the message being answered
    patient_ref: str | None = None  # local identifier only (spec §6.4)

    def summary(self) -> str:
        parts = [self.text] if self.text else []
        parts += [f"{VITALS[v.code][0]} {v.value:g} {VITALS[v.code][1]}" for v in self.vitals]
        parts += [f"[{a.title}, {len(a.data):,} bytes]" for a in self.attachments]
        return ", ".join(parts)


def encode(msg: Message, sent_at: str) -> bytes:
    for v in msg.vitals:
        if v.code not in VITALS:
            raise FhirError(f"unknown vital {v.code!r}")
    comm: dict[str, object] = {
        "resourceType": "Communication",
        "status": "completed",
        "sent": sent_at,
        "payload": [{"contentString": msg.text}],
    }
    if msg.in_response_to:
        comm["inResponseTo"] = [{"reference": f"Communication/{msg.in_response_to}"}]
    if msg.patient_ref:
        comm["subject"] = {"identifier": {"value": msg.patient_ref}}
    entries: list[dict[str, object]] = [comm]
    entries += [
        {
            "resourceType": "Observation",
            "status": "final",
            "code": {"coding": [{"code": v.code, "display": VITALS[v.code][0]}]},
            "valueQuantity": {"value": v.value, "unit": VITALS[v.code][1]},
            "effectiveDateTime": sent_at,
        }
        for v in msg.vitals
    ]
    entries += [
        {
            "resourceType": "DocumentReference",
            "status": "current",
            "content": [
                {"attachment": {"contentType": a.content_type, "title": a.title, "data": a.data}}
            ],
        }
        for a in msg.attachments
    ]
    return cbor2.dumps({"resourceType": "Bundle", "type": "message", "entry": entries})


def decode(data: bytes) -> Message:
    try:
        bundle = cbor2.loads(data)
        if bundle.get("resourceType") != "Bundle" or bundle.get("type") != "message":
            raise FhirError("not a FHIR-lite message bundle")
        comms = [e for e in bundle["entry"] if e["resourceType"] == "Communication"]
        if len(comms) != 1:
            raise FhirError("a message carries exactly one Communication")
        vitals, attachments = [], []
        for e in bundle["entry"]:
            kind = e["resourceType"]
            if kind == "Observation":
                code = e["code"]["coding"][0]["code"]
                if code not in VITALS:
                    raise FhirError(f"unknown vital {code!r}")
                vitals.append(Vital(code, float(e["valueQuantity"]["value"])))
            elif kind == "DocumentReference":
                a = e["content"][0]["attachment"]
                attachments.append(
                    Attachment(str(a["title"]), str(a["contentType"]), bytes(a["data"]))
                )
            elif kind != "Communication":
                raise FhirError(f"resource type {kind!r} not allowed")
        comm = comms[0]
        reply = comm.get("inResponseTo", [{}])[0].get("reference", "")
        return Message(
            text=str(comm["payload"][0]["contentString"]),
            vitals=tuple(vitals),
            attachments=tuple(attachments),
            in_response_to=reply.removeprefix("Communication/") or None,
            patient_ref=comm.get("subject", {}).get("identifier", {}).get("value"),
        )
    except (KeyError, IndexError, TypeError, AttributeError, cbor2.CBORDecodeError) as e:
        raise FhirError(f"malformed FHIR-lite message: {e}") from e
