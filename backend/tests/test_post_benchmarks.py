from datetime import UTC, datetime
from uuid import uuid4

import pytest
from test_posts import _headers, _post

from app.modules.posts.tools.benchmark import (
    BENCHMARK_DATASET_VERSION,
    BenchmarkCatalog,
    BenchmarkCategory,
    BenchmarkReview,
    CalibrationStatus,
    HumanCalibrationEngine,
    HumanDimensionReview,
    ReviewerExpertise,
)
from app.modules.posts.tools.quality import QualityDimension

pytest_plugins = ("test_posts",)


def _quality_report(score: float = 9.0) -> dict:
    return {
        "schema_version": "1.0",
        "decision": "PASS",
        "overall_score": score,
        "scores": [
            {
                "dimension": dimension.value,
                "score": score,
                "threshold": 8.5 if dimension.value in {
                    "marketing_effectiveness", "brand_fit", "product_fidelity", "audience_fit"
                } else 8.0,
                "critical": dimension.value in {
                    "marketing_effectiveness", "brand_fit", "product_fidelity", "audience_fit"
                },
                "passed": True,
                "evidence": ["Benchmark test evidence."],
                "source": ["test"],
            }
            for dimension in QualityDimension
        ],
        "failed_dimensions": [],
        "failed_hard_gates": [],
        "reason": "PASS: benchmark fixture meets every threshold.",
        "recommended_action": None,
        "thresholds": {
            "overall_minimum": 9.0,
            "critical_minimum": 8.5,
            "dimension_minimum": 8.0,
            "critical_dimensions": [
                "marketing_effectiveness", "brand_fit", "product_fidelity", "audience_fit"
            ],
            "weights": {},
        },
        "render_checksum": "b" * 64,
        "contract_fingerprint": "a" * 64,
    }


async def _generation_with_score(client, headers, *, score: float = 9.0) -> tuple[str, str]:
    post = await _post(client, headers)
    created = await client.post(f"/api/posts/{post['id']}/generations", headers=headers)
    assert created.status_code == 201
    generation = created.json()
    patched = await client.patch(
        f"/api/posts/{post['id']}/generations/{generation['id']}/state/quality_approval",
        headers=headers,
        json={"expected_version": 1, "value": _quality_report(score)},
    )
    assert patched.status_code == 200
    return post["id"], generation["id"]


def _submission(post_id: str, generation_id: str, *, human_score: float = 8.0) -> dict:
    return {
        "post_id": post_id,
        "generation_id": generation_id,
        "expertise": "designer",
        "human_score": human_score,
        "feedback": "Hierarchy is strong, but the product crop needs more breathing room.",
        "dimension_reviews": [
            {
                "dimension": "visual_hierarchy",
                "score": human_score,
                "feedback": "Headline reads first, but the crop is slightly aggressive.",
            }
        ],
    }


def test_catalog_has_one_valid_versioned_case_for_every_required_category() -> None:
    cases = BenchmarkCatalog().list()
    assert len(cases) == 11
    assert {case.category for case in cases} == set(BenchmarkCategory)
    assert all(case.dataset_version == BENCHMARK_DATASET_VERSION for case in cases)
    assert all(case.professional_references for case in cases)
    assert all(case.quality_labels for case in cases)


@pytest.mark.asyncio
async def test_api_lists_and_filters_the_versioned_dataset(post_client) -> None:
    response = await post_client.get("/api/post-benchmarks")
    assert response.status_code == 200
    assert len(response.json()) == 11

    filtered = await post_client.get("/api/post-benchmarks?category=rent-a-car")
    assert filtered.status_code == 200
    assert [item["category"] for item in filtered.json()] == ["rent-a-car"]

    calibration = await post_client.get("/api/post-benchmarks/calibration")
    assert calibration.status_code == 200
    assert calibration.json()["status"] == "insufficient_data"
    assert calibration.json()["mean_bias"] is None

    missing = await post_client.get("/api/post-benchmarks/not-a-real-case")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_human_review_uses_server_score_and_persists_signed_difference(post_client) -> None:
    headers = _headers()
    post_id, generation_id = await _generation_with_score(post_client, headers, score=9.0)

    response = await post_client.post(
        "/api/post-benchmarks/coffee-origin-launch/reviews",
        headers=headers,
        json=_submission(post_id, generation_id, human_score=7.5),
    )

    assert response.status_code == 201
    review = response.json()
    assert review["human_score"] == 7.5
    assert review["ai_score"] == 9.0
    assert review["difference"] == 1.5
    assert review["render_checksum"] == "b" * 64
    assert review["reviewer_user_id"] == headers["X-User-ID"]


@pytest.mark.asyncio
async def test_client_cannot_supply_its_own_ai_score(post_client) -> None:
    headers = _headers()
    post_id, generation_id = await _generation_with_score(post_client, headers)
    payload = {**_submission(post_id, generation_id), "ai_score": 10}
    response = await post_client.post(
        "/api/post-benchmarks/coffee-origin-launch/reviews",
        headers=headers,
        json=payload,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_review_requires_tenant_owned_generation_and_completed_quality(post_client) -> None:
    owner = _headers()
    post_id, generation_id = await _generation_with_score(post_client, owner)
    foreign = _headers()
    denied = await post_client.post(
        "/api/post-benchmarks/coffee-origin-launch/reviews",
        headers=foreign,
        json=_submission(post_id, generation_id),
    )
    assert denied.status_code == 404

    post = await _post(post_client, owner)
    generation = await post_client.post(
        f"/api/posts/{post['id']}/generations", headers=owner
    )
    not_ready = await post_client.post(
        "/api/post-benchmarks/coffee-origin-launch/reviews",
        headers=owner,
        json=_submission(post["id"], generation.json()["id"]),
    )
    assert not_ready.status_code == 409


@pytest.mark.asyncio
async def test_duplicate_human_submission_is_rejected(post_client) -> None:
    headers = _headers()
    post_id, generation_id = await _generation_with_score(post_client, headers)
    payload = _submission(post_id, generation_id)
    first = await post_client.post(
        "/api/post-benchmarks/coffee-origin-launch/reviews", headers=headers, json=payload
    )
    second = await post_client.post(
        "/api/post-benchmarks/coffee-origin-launch/reviews", headers=headers, json=payload
    )
    assert first.status_code == 201
    assert second.status_code == 409


def _review(
    ai: float,
    human: float,
    *,
    category=BenchmarkCategory.COFFEE,
    expertise=ReviewerExpertise.DESIGNER,
) -> BenchmarkReview:
    return BenchmarkReview(
        id=uuid4(),
        benchmark_slug="coffee-origin-launch",
        benchmark_version=BENCHMARK_DATASET_VERSION,
        category=category,
        generation_id=uuid4(),
        reviewer_user_id=uuid4(),
        project_id=uuid4(),
        expertise=expertise,
        human_score=human,
        ai_score=ai,
        ai_dimension_scores={QualityDimension.VISUAL_HIERARCHY: ai},
        difference=ai - human,
        feedback="The AI consistently overrates the visual hierarchy.",
        dimension_reviews=[
            HumanDimensionReview(
                dimension=QualityDimension.VISUAL_HIERARCHY,
                score=7,
                feedback="The product competes with the headline.",
            )
        ],
        render_checksum="c" * 64,
        created_at=datetime.now(UTC),
    )


def test_calibration_requires_samples_then_corrects_measured_bias() -> None:
    engine = HumanCalibrationEngine()
    reviews = [
        _review(9, 8),
        _review(8, 7, expertise=ReviewerExpertise.MARKETING_EXPERT),
        _review(10, 9),
    ]
    insufficient = engine.build(reviews, minimum_samples=5)
    assert insufficient.status is CalibrationStatus.INSUFFICIENT_DATA
    assert insufficient.mean_bias is None

    profile = engine.build(reviews, minimum_samples=3)
    assert profile.status is CalibrationStatus.READY
    assert profile.mean_bias == 1
    assert profile.mean_absolute_error == 1
    assert profile.recommended_offset == -1
    assert profile.calibrate(9.5) == 8.5
    assert profile.recurring_feedback == [QualityDimension.VISUAL_HIERARCHY.value]
    assert profile.dimension_offsets == {QualityDimension.VISUAL_HIERARCHY: -2}
