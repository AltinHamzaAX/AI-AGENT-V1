"""Decide every hard gate from evidence, never from a model's judgement.

The vision model is a witness: it enumerates what is legible and what identities
it recognises. Every PASS / BLOCKED call is made here, from the semantic
contract, the approved copy, the design spec and the draft's own component
record, so the same inputs always yield the same verdict and a blocked post can
be argued with rather than appealed to.

Nothing in this module reads a score. That is the point of the layer: a post
that fails one gate is blocked at any aesthetic score whatsoever.
"""

import re
import unicodedata
from dataclasses import dataclass
from uuid import UUID

from app.modules.posts.agents.asset_intelligence import IntelligentAssetRole
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.tools.composition import ComponentKind, ComponentMetadata

from .schemas import (
    GateCheck,
    GateFailure,
    RenderReadout,
    VerificationDecision,
    VerificationGate,
    VerificationInput,
)

#: Roles a product component is allowed to be composited from.
PRODUCT_ROLES = frozenset(
    {
        IntelligentAssetRole.PRIMARY_PRODUCT,
        IntelligentAssetRole.VEHICLE,
        IntelligentAssetRole.PACKAGING,
    }
)
#: Kinds whose pixels come from an approved original and must reach the export
#: unaltered. The scene is excluded on purpose: a generated plate has no
#: identity to preserve, and scene purity already certified it.
IDENTITY_KINDS = frozenset({ComponentKind.PRODUCT, ComponentKind.LOGO})
#: The largest export multiple the composer is allowed to produce.
MAX_EXPORT_SCALE = 4
#: A single stray glyph is noise in a photograph; two or more is a word.
MIN_TEXT_LENGTH = 2

CLEAN_DETAIL: dict[VerificationGate, str] = {
    VerificationGate.CORRECT_BRAND: "Only the post's own brand identity appears in the render.",
    VerificationGate.CORRECT_PRODUCT: "The render depicts the product the contract names.",
    VerificationGate.CORRECT_LOGO: "The logo region carries the approved brand-logo original.",
    VerificationGate.CORRECT_OFFER: "The approved offer is rendered with its stated terms.",
    VerificationGate.CORRECT_SPELLING: "Every rendered string is exactly the approved copy.",
    VerificationGate.REQUIRED_FACTS_PRESENT: "Every required fact appears in the published copy.",
    VerificationGate.REQUIRED_ASSETS_PRESENT: "Every required asset reached the composition.",
    VerificationGate.FORBIDDEN_CLAIMS_ABSENT: "No forbidden claim appears in the published copy.",
    VerificationGate.FAKE_BRANDING_ABSENT: "No brand mark appears that was not composited.",
    VerificationGate.UNWANTED_TEXT_ABSENT: "Every legible string belongs to the approved copy.",
    VerificationGate.CORRECT_DIMENSIONS: "The export matches the approved canvas geometry.",
    VerificationGate.ASSET_FIDELITY: "Every protected original reached the export unaltered.",
}

_TOKEN = re.compile(r"[a-z0-9]{3,}")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


@dataclass(frozen=True, slots=True)
class VerificationAssessment:
    decision: VerificationDecision
    checks: tuple[GateCheck, ...]
    failures: tuple[GateFailure, ...]


class _Ledger:
    """Collects one detail and its evidence per gate."""

    def __init__(self) -> None:
        self._reasons: dict[VerificationGate, list[str]] = {gate: [] for gate in VerificationGate}
        self._evidence: dict[VerificationGate, list[str]] = {gate: [] for gate in VerificationGate}

    def fail(self, gate: VerificationGate, reason: str, evidence: list[str] | None = None) -> None:
        self._reasons[gate].append(reason)
        for item in evidence or []:
            if item not in self._evidence[gate]:
                self._evidence[gate].append(item)

    def assess(self) -> VerificationAssessment:
        checks = tuple(
            GateCheck(
                gate=gate,
                passed=not self._reasons[gate],
                detail=_detail(gate, self._reasons[gate]) or CLEAN_DETAIL[gate],
            )
            for gate in VerificationGate
        )
        failures = tuple(
            GateFailure(
                gate=gate,
                detail=_detail(gate, self._reasons[gate]),
                evidence=self._evidence[gate][:20],
            )
            for gate in VerificationGate
            if self._reasons[gate]
        )
        decision = VerificationDecision.BLOCKED if failures else VerificationDecision.PASS
        return VerificationAssessment(decision=decision, checks=checks, failures=failures)


def decide_verification(
    readout: RenderReadout, *, payload: VerificationInput
) -> VerificationAssessment:
    contract = payload.contract()
    ledger = _Ledger()
    components = payload.post_draft.components
    approved = _approved_strings(payload)
    published = _published_blob(payload)

    _check_dimensions(ledger, payload)
    _check_asset_fidelity(ledger, payload)
    _check_required_assets(ledger, payload, contract)
    _check_logo(ledger, payload)
    _check_spelling(ledger, components, approved)
    _check_offer(ledger, payload, contract, published)
    _check_required_facts(ledger, contract, published)
    _check_forbidden_claims(ledger, contract, published)
    _check_brand(ledger, readout, payload, contract)
    _check_fake_branding(ledger, readout, payload)
    _check_unwanted_text(ledger, readout, contract, approved)
    _check_product(ledger, readout, payload, contract)
    return ledger.assess()


def _check_dimensions(ledger: _Ledger, payload: VerificationInput) -> None:
    canvas = payload.design_spec.canvas
    working = payload.post_draft.working_render
    final = payload.post_draft.final_asset
    if (working.width, working.height) != (canvas.width, canvas.height):
        ledger.fail(
            VerificationGate.CORRECT_DIMENSIONS,
            f"the working render is {working.width}x{working.height} but the approved canvas "
            f"is {canvas.width}x{canvas.height}",
        )
    if final.width % canvas.width or final.height % canvas.height:
        ledger.fail(
            VerificationGate.CORRECT_DIMENSIONS,
            f"the export is {final.width}x{final.height}, which is not a whole multiple of the "
            f"approved {canvas.width}x{canvas.height} canvas",
        )
        return
    scale = final.width // canvas.width
    if scale != final.height // canvas.height:
        ledger.fail(
            VerificationGate.CORRECT_DIMENSIONS,
            f"the export scales width and height differently, so the approved "
            f"{canvas.width}x{canvas.height} aspect ratio did not survive",
        )
    elif not 1 <= scale <= MAX_EXPORT_SCALE:
        ledger.fail(
            VerificationGate.CORRECT_DIMENSIONS,
            f"the export is {scale}x the approved canvas, outside the 1x-{MAX_EXPORT_SCALE}x range",
        )


def _check_asset_fidelity(ledger: _Ledger, payload: VerificationInput) -> None:
    policies = {policy.asset_id: policy for policy in payload.asset_policies}
    for component in payload.post_draft.components:
        if component.kind not in IDENTITY_KINDS:
            continue
        if component.source_asset_id is None:
            ledger.fail(
                VerificationGate.ASSET_FIDELITY,
                f"the {component.kind.value} component was rendered from no approved original",
                [component.component_id],
            )
            continue
        policy = policies.get(component.source_asset_id)
        if policy is None:
            ledger.fail(
                VerificationGate.ASSET_FIDELITY,
                f"asset {component.source_asset_id} reached the render without an approved policy",
                [str(component.source_asset_id)],
            )
            continue
        if component.source_checksum is None:
            ledger.fail(
                VerificationGate.ASSET_FIDELITY,
                f"the {component.kind.value} component records no source checksum, so its "
                "original cannot be identified",
                [component.component_id],
            )
        if policy.preserve_identity and component.identity_preserved is not True:
            ledger.fail(
                VerificationGate.ASSET_FIDELITY,
                f"identity-protected asset {policy.asset_id} was rendered with "
                f"identity_preserved={component.identity_preserved!r}",
                [str(policy.asset_id)],
            )


def _check_required_assets(
    ledger: _Ledger, payload: VerificationInput, contract: PostSemanticContract
) -> None:
    used = {
        component.source_asset_id
        for component in payload.post_draft.components
        if component.source_asset_id is not None
    }
    required: set[UUID] = set(contract.required_assets)
    required.update(policy.asset_id for policy in payload.asset_policies if policy.required)
    missing = sorted(required - used, key=str)
    if missing:
        ledger.fail(
            VerificationGate.REQUIRED_ASSETS_PRESENT,
            f"{len(missing)} required asset(s) never reached the composition",
            [str(asset_id) for asset_id in missing],
        )


def _check_logo(ledger: _Ledger, payload: VerificationInput) -> None:
    approved = {
        policy.asset_id: policy
        for policy in payload.asset_policies
        if policy.role is IntelligentAssetRole.BRAND_LOGO
    }
    rendered = [
        component
        for component in payload.post_draft.components
        if component.kind is ComponentKind.LOGO
    ]
    if not approved:
        if rendered:
            ledger.fail(
                VerificationGate.CORRECT_LOGO,
                "a logo was rendered although no brand-logo asset was approved",
                [component.component_id for component in rendered],
            )
        return
    if not rendered:
        if any(policy.required for policy in approved.values()):
            ledger.fail(
                VerificationGate.CORRECT_LOGO,
                "the approved brand logo is required but no logo component was rendered",
                [str(asset_id) for asset_id in sorted(approved, key=str)],
            )
        return
    for component in rendered:
        if component.source_asset_id not in approved:
            ledger.fail(
                VerificationGate.CORRECT_LOGO,
                f"the logo region carries asset {component.source_asset_id}, which is not the "
                "approved brand logo",
                [str(component.source_asset_id)],
            )


def _check_spelling(
    ledger: _Ledger, components: list[ComponentMetadata], approved: dict[str, str]
) -> None:
    """Approved copy already passed its own quality gate; this proves it shipped.

    Comparing the rendered string against the approved one catches every way the
    composer can change words on the way to the export - truncation, an ellipsis,
    a dropped glyph, a substituted character - without needing a dictionary or
    knowing the post's language.
    """
    for component in components:
        if component.text is None:
            continue
        rendered = _clean(component.text)
        if _semantic(rendered) not in approved:
            ledger.fail(
                VerificationGate.CORRECT_SPELLING,
                f"the {component.kind.value} component renders text that is not the approved copy",
                [rendered],
            )


def _check_offer(
    ledger: _Ledger,
    payload: VerificationInput,
    contract: PostSemanticContract,
    published: str,
) -> None:
    if contract.offer is None:
        return
    offer_copy = payload.copy_draft.offer_copy
    if offer_copy is None:
        ledger.fail(
            VerificationGate.CORRECT_OFFER,
            f"the contract declares the offer {contract.offer!r} but the copy carries none",
            [contract.offer],
        )
        return
    rendered = {
        _semantic(component.text)
        for component in payload.post_draft.components
        if component.text is not None
    }
    if _semantic(offer_copy) not in rendered:
        ledger.fail(
            VerificationGate.CORRECT_OFFER,
            "the approved offer copy was never rendered into the post",
            [offer_copy],
        )
    # An offer that loses or changes its numbers is a different offer, and it is
    # the one failure here a reader would act on.
    published_numbers = set(_NUMBER.findall(published))
    dropped = [
        number
        for number in _NUMBER.findall(_semantic(contract.offer))
        if number not in published_numbers
    ]
    if dropped:
        ledger.fail(
            VerificationGate.CORRECT_OFFER,
            "the published copy does not state the offer's own figures",
            dropped,
        )


def _check_required_facts(ledger: _Ledger, contract: PostSemanticContract, published: str) -> None:
    available = set(_TOKEN.findall(published)) | set(_NUMBER.findall(published))
    for name, value in contract.required_facts:
        wanted = set(_TOKEN.findall(_semantic(value))) | set(_NUMBER.findall(_semantic(value)))
        missing = sorted(wanted - available)
        if missing:
            ledger.fail(
                VerificationGate.REQUIRED_FACTS_PRESENT,
                f"required fact {name!r} is not stated in the published copy",
                [f"{name}: {value}"],
            )


def _check_forbidden_claims(
    ledger: _Ledger, contract: PostSemanticContract, published: str
) -> None:
    for claim in contract.forbidden_claims:
        if _semantic(claim) in published:
            ledger.fail(
                VerificationGate.FORBIDDEN_CLAIMS_ABSENT,
                "the published copy makes a claim the contract forbids",
                [claim],
            )


def _check_brand(
    ledger: _Ledger,
    readout: RenderReadout,
    payload: VerificationInput,
    contract: PostSemanticContract,
) -> None:
    own = _identity_terms(contract.brand, contract.company)
    if not own:
        return
    # A string the render legitimately carries can be misread as an identity, so
    # only a name that matches neither the post's brand nor its own copy counts.
    familiar = own | set(_TOKEN.findall(_published_text(payload)))
    foreign = [name for name in readout.visible_brands if not _matches(name, familiar)]
    if foreign:
        ledger.fail(
            VerificationGate.CORRECT_BRAND,
            "the render shows an identity that is not the one this post is for",
            foreign,
        )
    logo_rendered = any(
        component.kind is ComponentKind.LOGO for component in payload.post_draft.components
    )
    if not logo_rendered and not _matches_any(own, _published_text(payload)):
        ledger.fail(
            VerificationGate.CORRECT_BRAND,
            f"the post is for {contract.brand or contract.company!r} but the render neither "
            "names it nor carries its logo",
        )


def _check_fake_branding(
    ledger: _Ledger, readout: RenderReadout, payload: VerificationInput
) -> None:
    if not readout.visible_brands:
        return
    approved = {
        policy.asset_id
        for policy in payload.asset_policies
        if policy.role is IntelligentAssetRole.BRAND_LOGO
    }
    composited = any(
        component.kind is ComponentKind.LOGO and component.source_asset_id in approved
        for component in payload.post_draft.components
    )
    if not composited:
        # The real mark is composited from an approved original, never drawn, so
        # a visible mark with nothing behind it was invented somewhere upstream.
        ledger.fail(
            VerificationGate.FAKE_BRANDING_ABSENT,
            "a brand mark is visible although no approved logo original was composited",
            list(readout.visible_brands),
        )


def _check_unwanted_text(
    ledger: _Ledger,
    readout: RenderReadout,
    contract: PostSemanticContract,
    approved: dict[str, str],
) -> None:
    """Flag only strings that share nothing with the approved copy.

    A small vision model misreads glyphs, so demanding an exact match would
    block good posts on its transcription rather than on the render. A string
    whose every token is foreign is not a misreading, it is other text.
    """
    known = set(_identity_terms(contract.brand, contract.company))
    for original in approved.values():
        known.update(_TOKEN.findall(_semantic(original)))
        known.update(_NUMBER.findall(_semantic(original)))
    unwanted: list[str] = []
    for visible in readout.visible_text:
        if len(visible) < MIN_TEXT_LENGTH:
            continue
        normalized = _semantic(visible)
        tokens = set(_TOKEN.findall(normalized)) | set(_NUMBER.findall(normalized))
        if tokens and not tokens & known:
            unwanted.append(visible)
    if unwanted:
        ledger.fail(
            VerificationGate.UNWANTED_TEXT_ABSENT,
            "the render carries legible text that belongs to no approved copy",
            unwanted,
        )


def _check_product(
    ledger: _Ledger,
    readout: RenderReadout,
    payload: VerificationInput,
    contract: PostSemanticContract,
) -> None:
    approved_assets = {
        policy.asset_id for policy in payload.asset_policies if policy.role in PRODUCT_ROLES
    }
    rendered = [
        component
        for component in payload.post_draft.components
        if component.kind is ComponentKind.PRODUCT
    ]
    for component in rendered:
        if component.source_asset_id not in approved_assets:
            ledger.fail(
                VerificationGate.CORRECT_PRODUCT,
                f"the product region carries asset {component.source_asset_id}, which no "
                "approved product policy covers",
                [str(component.source_asset_id)],
            )
    approved_subject = contract.product or contract.primary_entity
    subject = _identity_terms(contract.product, contract.primary_entity)
    unrelated = [name for name in readout.depicted_products if not _matches(name, subject)]
    if subject and unrelated:
        ledger.fail(
            VerificationGate.CORRECT_PRODUCT,
            f"the render depicts a subject other than {approved_subject!r}",
            unrelated,
        )


def _approved_strings(payload: VerificationInput) -> dict[str, str]:
    """Every string the composer was allowed to render, keyed by its normal form."""
    copy = payload.copy_draft
    candidates = [
        copy.headline,
        copy.subheadline,
        copy.supporting_copy,
        copy.offer_copy,
        copy.cta,
        payload.legal_text,
    ]
    return {_semantic(value): _clean(value) for value in candidates if value}


def _published_text(payload: VerificationInput) -> str:
    """Everything that ships: the render's words plus the caption they travel with."""
    copy = payload.copy_draft
    parts = [
        *_approved_strings(payload).values(),
        copy.caption,
        *copy.hashtags,
        *(
            component.text
            for component in payload.post_draft.components
            if component.text is not None
        ),
    ]
    return " ".join(parts)


def _published_blob(payload: VerificationInput) -> str:
    return _semantic(_published_text(payload))


def _clean(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _semantic(value: str) -> str:
    return _clean(value).casefold()


def _identity_terms(*values: str | None) -> set[str]:
    terms: set[str] = set()
    for value in values:
        if not value:
            continue
        terms.add(_semantic(value))
        terms.update(_TOKEN.findall(_semantic(value)))
    return terms


def _matches(candidate: str, terms: set[str]) -> bool:
    if not terms:
        return False
    normalized = _semantic(candidate)
    if normalized in terms:
        return True
    return bool(set(_TOKEN.findall(normalized)).intersection(terms))


def _matches_any(terms: set[str], text: str) -> bool:
    available = set(_TOKEN.findall(_semantic(text)))
    return bool(terms & available)


def _detail(gate: VerificationGate, reasons: list[str]) -> str:
    if not reasons:
        return ""
    joined = "; ".join(dict.fromkeys(reasons))
    return f"{gate.value}: {joined}"[:600]


__all__ = [
    "CLEAN_DETAIL",
    "IDENTITY_KINDS",
    "MAX_EXPORT_SCALE",
    "MIN_TEXT_LENGTH",
    "PRODUCT_ROLES",
    "VerificationAssessment",
    "decide_verification",
]
