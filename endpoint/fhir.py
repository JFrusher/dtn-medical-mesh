"""FHIR-lite clinical payloads (spec §6.4), encoded as CBOR so attachments stay binary.

A message is a FHIR-shaped Bundle of type "message" holding:
  Communication      the text (one per message), its author, inResponseTo for threads
  Patient            name, age, sex and local ID only (spec §10.3), at most one
  ServiceRequest     a structured referral: reason, working diagnosis, history, treatment given
  Task               the hospital's answer to a referral: accepted, declined or needs information
  DiagnosticReport   a result sent back to the clinic: test and conclusion
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


SEXES = ("female", "male", "other", "unknown")
DECISIONS = {"accepted": "accepted", "declined": "rejected", "needs-info": "on-hold"}  # -> Task


class FhirError(ValueError):
    pass


@dataclass(frozen=True)
class Patient:
    local_id: str
    name: str
    age: int
    sex: str  # one of SEXES

    def __post_init__(self) -> None:
        if self.sex not in SEXES or not 0 <= self.age <= 130 or not self.local_id.strip():
            raise FhirError("patient needs a local ID, an age of 0-130 and a sex from SEXES")


@dataclass(frozen=True)
class Referral:
    reason: str
    diagnosis: str = ""
    history: str = ""
    treatment: str = ""


@dataclass(frozen=True)
class Result:
    test: str
    conclusion: str


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
    author: str | None = None  # the staff member who wrote it (spec §10.2)
    patient: Patient | None = None
    referral: Referral | None = None
    decision: str | None = None  # a key of DECISIONS: the answer to the referral in_response_to
    result: Result | None = None

    def __post_init__(self) -> None:
        if self.decision is not None and (
            self.decision not in DECISIONS or not self.in_response_to
        ):
            raise FhirError(
                "a decision answers a referral: give in_response_to and a known decision"
            )

    def summary(self) -> str:
        parts = [f"Referral: {self.referral.reason}"] if self.referral else []
        parts += [f"Result: {self.result.test}, {self.result.conclusion}"] if self.result else []
        parts += [self.text] if self.text else []
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
    if msg.author:
        comm["sender"] = {"display": msg.author}
    entries: list[dict[str, object]] = [comm]
    if p := msg.patient:
        entries.append(
            {
                "resourceType": "Patient",
                "identifier": [{"value": p.local_id}],
                "name": [{"text": p.name}],
                "gender": p.sex,
                "extension": [{"url": "age", "valueAge": {"value": p.age, "unit": "a"}}],
            }
        )
    if r := msg.referral:
        extra = {"diagnosis": r.diagnosis, "history": r.history, "treatment": r.treatment}
        entries.append(
            {
                "resourceType": "ServiceRequest",
                "status": "active",
                "intent": "order",
                "reasonCode": [{"text": r.reason}],
                "extension": [{"url": k, "valueString": v} for k, v in extra.items() if v],
            }
        )
    if msg.decision:
        entries.append(
            {
                "resourceType": "Task",
                "status": DECISIONS[msg.decision],
                "intent": "order",
                "focus": {"reference": f"ServiceRequest/{msg.in_response_to}"},
            }
        )
    if res := msg.result:
        entries.append(
            {
                "resourceType": "DiagnosticReport",
                "status": "final",
                "code": {"text": res.test},
                "conclusion": res.conclusion,
            }
        )
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
        patient = referral = decision = result = None
        for e in bundle["entry"]:
            kind = e["resourceType"]
            once = kind in ("Patient", "ServiceRequest", "Task", "DiagnosticReport")
            if once and sum(x["resourceType"] == kind for x in bundle["entry"]) > 1:
                raise FhirError(f"at most one {kind} per message")
            if kind == "Patient":
                age = next(x["valueAge"]["value"] for x in e["extension"] if x["url"] == "age")
                patient = Patient(
                    str(e["identifier"][0]["value"]),
                    str(e["name"][0]["text"]),
                    int(age),
                    e["gender"],
                )
            elif kind == "ServiceRequest":
                extra = {x["url"]: str(x["valueString"]) for x in e.get("extension", [])}
                if set(extra) - {"diagnosis", "history", "treatment"}:
                    raise FhirError("unknown referral field")
                referral = Referral(str(e["reasonCode"][0]["text"]), **extra)
            elif kind == "Task":
                by_status = {v: k for k, v in DECISIONS.items()}
                if e["status"] not in by_status:
                    raise FhirError(f"unknown decision {e['status']!r}")
                decision = by_status[e["status"]]
            elif kind == "DiagnosticReport":
                result = Result(str(e["code"]["text"]), str(e["conclusion"]))
            elif kind == "Observation":
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
            author=comm.get("sender", {}).get("display"),
            patient=patient,
            referral=referral,
            decision=decision,
            result=result,
        )
    except FhirError:
        raise
    except (
        KeyError,
        IndexError,
        TypeError,
        AttributeError,
        ValueError,
        StopIteration,
        cbor2.CBORDecodeError,
    ) as e:
        raise FhirError(f"malformed FHIR-lite message: {e}") from e
