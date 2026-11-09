"""Accessibility (spec §10.1): an axe-core scan of every staff-app state in light and dark
mode, keyboard-only sign-in and referral, and 44 px touch targets in the staff app."""

import json
from pathlib import Path
from typing import Literal

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from simulation.live import DEMO_PIN, LiveSim
from simulation.runner import build
from simulation.scenario import load_scenario
from validation.interface.test_ui import serve, sign_in

AXE = (Path(__file__).parent / "vendor/axe.min.js").read_text()
RULES = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"]


def violations(page: Page) -> list[str]:
    page.add_script_tag(content=AXE)
    found = page.evaluate(
        "async tags => (await axe.run(document, {runOnly: tags})).violations"
        ".map(v => `${v.id}: ${v.nodes.map(n => n.target.join(' ')).slice(0, 3).join(', ')}`)",
        RULES,
    )
    return list(found)


@pytest.mark.req("REQ-APP-07")
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_no_axe_violations_in_any_state(tmp_path: Path, scheme: Literal["light", "dark"]) -> None:
    sim = LiveSim(build(load_scenario("skeleton"), 1, tmp_path), speed=600)
    for url in serve(sim):
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900}, color_scheme=scheme)
            page.goto(url)
            found: dict[str, list[str]] = {"closed": violations(page)}
            page.locator(".open.left").click()
            page.locator(".open.right").click()
            found["sign-in"] = violations(page)
            left, right = page.locator("#left"), page.locator("#right")
            sign_in(left)
            sign_in(right)
            expect(right.locator(".msg.in").first).to_be_visible(timeout=20_000)
            found["cases and triage"] = violations(page)
            left.get_by_role("tab", name="New referral").click()
            right.get_by_role("tab", name="Write").click()
            found["forms"] = violations(page)
            left.get_by_role("tab", name="Patients").click()
            found["patients"] = violations(page)
            page.locator(".views button[data-view=journey]").click()
            found["journey"] = violations(page)
            browser.close()
        assert {k: v for k, v in found.items() if v} == {}, json.dumps(found, indent=1)


@pytest.mark.req("REQ-APP-07")
def test_keyboard_only_sign_in_and_referral(tmp_path: Path) -> None:
    sim = LiveSim(build(load_scenario("skeleton"), 1, tmp_path), speed=60)
    for url in serve(sim):
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(url)
            page.locator(".open.left").focus()
            page.keyboard.press("Enter")
            left = page.locator("#left")
            expect(left).to_be_visible()
            left.locator("input[type=password]").focus()
            page.keyboard.type(DEMO_PIN)
            page.keyboard.press("Enter")  # submits the form
            expect(left.get_by_role("tab", name="Cases")).to_be_visible()
            left.get_by_role("tab", name="New referral").focus()
            page.keyboard.press("Enter")
            left.locator("[name=reason]").focus()
            page.keyboard.type("keyboard-only referral")
            left.locator(".send").focus()
            page.keyboard.press("Enter")
            expect(left.locator(".msg.out", has_text="keyboard-only referral")).to_be_visible()
            small = page.evaluate(
                """[...document.querySelectorAll(
                     '#left button, #left select, #left input:not([type=file])')]
                   .filter(e => e.offsetParent)
                   .map(e => [e.className || e.tagName, e.getBoundingClientRect().height])
                   .filter(([, h]) => h < 44)"""
            )
            browser.close()
        assert small == [], small


@pytest.mark.req("REQ-OPS-03", "REQ-APP-07")
def test_admin_console_revokes_from_the_page_and_passes_axe(tmp_path: Path) -> None:
    sim = LiveSim(build(load_scenario("skeleton"), 1, tmp_path), speed=600)
    for url in serve(sim):
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.on("dialog", lambda d: d.accept())  # "signed with your key and logged. Continue?"
            page.goto(f"{url}/admin")
            page.locator("#pin").fill(DEMO_PIN)
            page.get_by_role("button", name="Sign in").click()
            expect(page.locator("#inventory")).to_contain_text("Ambulance 01")
            page.locator("[name=action]").select_option("revoke")
            page.locator("[name=target]").fill("AMBULANCE_01")
            page.get_by_role("button", name="Do it").click()
            expect(page.locator("#log")).to_contain_text("Revoke node")
            expect(page.locator("#inventory")).to_contain_text("revoked")
            found = violations(page)
            page.screenshot(path=Path(__file__).parents[2] / "out/admin.png", full_page=True)
            browser.close()
        assert found == [], found
