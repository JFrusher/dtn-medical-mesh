# Satchel — pilot prospectus

*A five-minute read for a prospective clinical, NGO or research partner. It summarises what the
system does, what has been shown so far, and what a supervised pilot would ask of you. Everything
here is backed by the project's own documents — the sizing study ([docs/sizing/arusha.md](sizing/arusha.md)),
the hazard log ([docs/hazard_log.md](hazard_log.md)) and the specification ([PROJECT_SPEC.md](../PROJECT_SPEC.md)) — so you can check each claim.*

> [!IMPORTANT]
> **Nothing here is certified or deployed, and no result is a performance claim.** Every figure
> comes from an emulator running on estimated message volumes and radio conditions. A pilot exists
> precisely to replace those estimates with your real numbers. The system is **not for emergencies.**

---

## The problem

Rural clinics, remote stations and disaster sites often have local power but no reliable internet.
A referral note, a photo of a wound, a set of vitals, a lab result — these wait on a local device
until a person physically carries them, or until the patient is moved. Meanwhile, ambulances, supply
vans and outreach staff drive between those same sites every day.

## What Satchel does

It turns that existing physical movement into a secure data link. A clinician writes a referral in a
familiar messaging-style app on a clinic laptop. Small battery-and-solar nodes — mounted on a clinic
shelf, in an ambulance, on a street light — automatically swap **encrypted pieces** of the message
whenever they come within radio range, with no one pressing a button. The pieces travel the roads the
vehicles already drive. When enough reach the hospital, the message is rebuilt and opened, and a
signed confirmation makes its way back.

Two properties define it:

- **No silent loss.** Every message is either confirmed delivered, or the origin clinic is plainly
  told *not delivered* so staff can use another channel. Nothing disappears unnoticed.
- **Couriers are blind.** Relay nodes carry data they can never read; clinical content is encrypted
  end to end, and no relay holds a key that could open it. A stolen or malicious node cannot expose a
  patient's data.

Crews see only a status light. Staff do no more work than sending a WhatsApp message.

## What has been shown so far (in emulation)

A full emulation of a day in the Arusha district — four clinics, a regional hospital, an ambulance,
two vans, an outreach motorbike and solar relays — gives these results. Figures are medians with
95% intervals over ten simulated days; read them as direction, not as guarantees.

- **No message was ever lost without warning:** 0 invariant violations across 80 runs, including
  under attack (lossy radio, flipped bits, power cuts, clocks reset to 1970, a node forging messages,
  a node swallowing everything, an unregistered device). Thousands of bad frames were refused.
- **Critical referrals arrived in time.** With the ambulance on the road, 100% of critical referrals
  that had four hours to arrive did so.
- **The ambulance is the one dependency to protect.** Without it, critical-referral delivery inside
  four hours fell to 27%. A second vehicle on that road is the first thing a pilot should add.
- **Power and storage are not the limits** at these volumes: solar relays held above 94% charge in
  the modelled year, and the busiest node used 3.4 MB of a 32 GB card even at double the message
  volume. The network is limited by *when vehicles meet*, not by losses or capacity.
- **Radio range matters most.** Worse path loss (≈60 m instead of ≈90 m) was the change that hurt
  delivery. Measuring real range and mounting antennas well is worth more than adding relays.

A companion study, [docs/sizing/sensitivity.md](sizing/sensitivity.md), stress-tests these promises
against hardware that performs *worse* than modelled, so the pilot plans against a floor, not a
best case.

## Honest limits

- **Not for emergency or time-critical communication.** Delivery depends on vehicles moving and can
  take hours; the system never promises a time, and says so on every message.
- **Not certified and not a medical record system.** The design follows the structure of IEC 62304,
  ISO 14971 and GDPR Article 9 so a clinical partner can build on it, but no conformity is claimed.
- **The numbers are estimates.** Message volumes and vehicle schedules are assumed, not measured.

## What a pilot looks like

The route to a real deployment has three stages; the first is complete.

| Stage | What it is | Status |
|---|---|---|
| **Simulation** | Whole-district emulation, sizing and attack scenarios | **Done** |
| **Bench (H1)** | 2–6 real nodes on a desk: measure link-up time, throughput per contact, hold-up through power loss, LoRa range, temperature | Next, needs hardware |
| **Field pilot (H2)** | ~10–30 nodes at real sites, running **in parallel** with existing channels, measured against the hazard log | Needs a partner |

### What the project brings

- The node software, the clinic staff app, and an operator admin console (provision and revoke nodes,
  push signed updates, see delivery times and node health).
- A hazard log and a Data Protection Impact Assessment template, pre-aligned to ISO 14971 and GDPR
  Article 9, ready for your Clinical Safety Officer to adopt and extend.
- The emulator, so your real volumes and vehicle logs can be modelled before any node is installed.

### What the pilot needs from you

These are the open items the engineering cannot supply on its own ([hazard log](hazard_log.md),
"Open items for the clinical partner"):

1. **Real referral volumes and vehicle schedules** for one or more routes, to replace the estimates.
2. **A Clinical Safety Officer** to confirm hazard severities and probabilities and own the clinical
   risk file.
3. **A defined parallel emergency channel** and an agreed response to a *not delivered* alert — this
   is the single most important safety condition.
4. **Site permissions and a local contact** at each clinic and the hospital.
5. **Workflow specifics** — for example, how results should route to the correct ward — so hazards
   particular to your setting are added before go-live.

## Indicative cost

Hardware only, and **indicative pending a bench build** — real figures come from H1. Nodes are
commodity single-board computers (Raspberry Pi Zero 2 W relays, a Pi 5 class hub) with a radio, a
small solar panel and battery, and a weatherproof enclosure. A relay node is in the low tens to low
hundreds of US dollars depending on enclosure and power; a ten-node pilot is a modest capital cost
dominated by enclosures, mounting and installation labour rather than the computers. There is no
per-message or connectivity cost — the system uses no cellular or internet service.

## The ask

If you run referrals between sites that lose connectivity, we would like to:

- model **your** routes and volumes in the emulator, at no cost and with no hardware, to show what
  delivery would look like for you; and
- scope a small supervised pilot that runs **alongside** your current way of working, never
  replacing it, measured honestly against the hazard log.

The system is deliberately built to fail safe and to tell the truth about what it did or did not
deliver. A pilot is how we find out whether that holds on real roads.
