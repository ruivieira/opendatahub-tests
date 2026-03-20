import pytest
import requests
from ocp_resources.namespace import Namespace
from ocp_resources.role_binding import RoleBinding
from ocp_resources.route import Route

from tests.model_explainability.evalhub.constants import (
    EVALHUB_TEST_BENCHMARK_ID,
    EVALHUB_TEST_PROVIDER_ID,
)
from tests.model_explainability.evalhub.utils import (
    create_evalhub_job,
    delete_evalhub_job,
    get_evalhub_provider,
    list_evalhub_providers,
    wait_for_job_completion,
)

# Dummy model URL/name — the test provider ignores the model endpoint.
_TEST_MODEL_URL: str = "http://test-model.test-evalhub-team-a.svc.cluster.local:8000"
_TEST_MODEL_NAME: str = "test-model"


@pytest.mark.sanity
@pytest.mark.model_explainability
class TestEvalHubTestProviderVisibility:
    """Verify that the test provider ConfigMap is picked up and visible via the providers API."""

    def test_test_provider_visible_to_team_a(
        self,
        evalhub_test_provider_ready: None,
        evalhub_providers_response: dict,
    ) -> None:
        """Team-a (authorised tenant) can see the test provider in the providers list."""
        provider_ids = [p["resource"]["id"] for p in evalhub_providers_response.get("items", [])]
        assert EVALHUB_TEST_PROVIDER_ID in provider_ids, (
            f"Expected '{EVALHUB_TEST_PROVIDER_ID}' in providers list, got: {provider_ids}"
        )

    def test_test_provider_has_datafile_benchmark(
        self,
        evalhub_test_provider_ready: None,
        evalhub_providers_response: dict,
    ) -> None:
        """The test provider exposes the datafile benchmark."""
        test_provider = next(
            (p for p in evalhub_providers_response.get("items", []) if p["resource"]["id"] == EVALHUB_TEST_PROVIDER_ID),
            None,
        )
        assert test_provider is not None, "Test provider not found in providers list"
        benchmark_ids = [b["id"] for b in test_provider.get("benchmarks", [])]
        assert EVALHUB_TEST_BENCHMARK_ID in benchmark_ids, (
            f"Expected benchmark '{EVALHUB_TEST_BENCHMARK_ID}' in test provider, got: {benchmark_ids}"
        )

    def test_get_test_provider_by_id(
        self,
        evalhub_test_provider_ready: None,
        evalhub_scoped_token: str,
        evalhub_ca_bundle_file: str,
        evalhub_route: Route,
        evalhub_team_a_namespace: Namespace,
    ) -> None:
        """Team-a can retrieve the test provider directly by ID."""
        data = get_evalhub_provider(
            host=evalhub_route.host,
            token=evalhub_scoped_token,
            ca_bundle_file=evalhub_ca_bundle_file,
            provider_id=EVALHUB_TEST_PROVIDER_ID,
            tenant=evalhub_team_a_namespace.name,
        )
        assert data["resource"]["id"] == EVALHUB_TEST_PROVIDER_ID
        assert "benchmarks" in data
        assert any(b["id"] == EVALHUB_TEST_BENCHMARK_ID for b in data["benchmarks"])

    def test_test_provider_visible_to_team_b(
        self,
        evalhub_test_provider_ready: None,
        evalhub_unauthorised_token: str,
        evalhub_ca_bundle_file: str,
        evalhub_route: Route,
        evalhub_team_b_namespace: Namespace,
        evalhub_providers_role_binding: RoleBinding,
    ) -> None:
        """Team-b users without providers RBAC are rejected with 400 (SAR DecisionNoOpinion)."""
        with pytest.raises(requests.exceptions.HTTPError, match="400"):
            list_evalhub_providers(
                host=evalhub_route.host,
                token=evalhub_unauthorised_token,
                ca_bundle_file=evalhub_ca_bundle_file,
                tenant=evalhub_team_b_namespace.name,
            )


@pytest.mark.sanity
@pytest.mark.model_explainability
class TestEvalHubJobLifecycle:
    """Full job lifecycle test: create → poll → verify completion → read results → cleanup."""

    def test_job_lifecycle_with_test_provider(
        self,
        evalhub_test_provider_ready: None,
        evalhub_evaluations_role_binding: RoleBinding,
        evalhub_scoped_token: str,
        evalhub_ca_bundle_file: str,
        evalhub_route: Route,
        evalhub_team_a_namespace: Namespace,
    ) -> None:
        """Submit a datafile job via the test provider, wait for completion, and verify results."""
        job_config = {
            "name": "test-datafile-job",
            "model": {
                "url": _TEST_MODEL_URL,
                "name": _TEST_MODEL_NAME,
            },
            "benchmarks": [
                {
                    "id": EVALHUB_TEST_BENCHMARK_ID,
                    "provider_id": EVALHUB_TEST_PROVIDER_ID,
                }
            ],
        }

        # Create the job.
        created = create_evalhub_job(
            host=evalhub_route.host,
            token=evalhub_scoped_token,
            ca_bundle_file=evalhub_ca_bundle_file,
            tenant=evalhub_team_a_namespace.name,
            job_config=job_config,
        )
        assert "resource" in created, f"Created job response missing 'resource': {created}"
        job_id = created["resource"]["id"]
        assert job_id, "Created job ID must not be empty"

        try:
            # Poll until the job reaches a terminal state.
            final = wait_for_job_completion(
                host=evalhub_route.host,
                token=evalhub_scoped_token,
                ca_bundle_file=evalhub_ca_bundle_file,
                tenant=evalhub_team_a_namespace.name,
                job_id=job_id,
                timeout=600,
                poll_interval=10,
            )

            # Verify the job completed successfully.
            state = final.get("status", {}).get("state", "")
            assert state == "completed", (
                f"Expected job state 'completed', got '{state}'. Full response: {final}"
            )

            # Verify results contain benchmark metrics.
            results = final.get("results", {})
            benchmarks = results.get("benchmarks", [])
            assert len(benchmarks) > 0, "Expected at least one benchmark in results"

            benchmark_result = benchmarks[0]
            assert "id" in benchmark_result, "Benchmark result missing 'id'"
            assert "metrics" in benchmark_result, "Benchmark result missing 'metrics'"
            assert len(benchmark_result["metrics"]) > 0, "Expected at least one metric in benchmark result"

        finally:
            # Clean up: cancel/delete the job regardless of outcome.
            try:
                delete_evalhub_job(
                    host=evalhub_route.host,
                    token=evalhub_scoped_token,
                    ca_bundle_file=evalhub_ca_bundle_file,
                    tenant=evalhub_team_a_namespace.name,
                    job_id=job_id,
                )
            except Exception:
                pass  # Best-effort cleanup; don't mask original assertion failures.
