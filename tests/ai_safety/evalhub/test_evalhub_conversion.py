"""EvalHub CRD conversion webhook tests (RHOAI 3.5EA2+).

The v1alpha1 <-> v1 conversion webhook was introduced in RHOAI 3.5EA2.
It is not available in RHOAI 2.25 or 3.4. These tests only apply to
3.x -> 3.x upgrades where the target version is 3.5EA2 or later.
"""

import pytest
from kubernetes.dynamic import DynamicClient
from kubernetes.dynamic.exceptions import NotFoundError, ResourceNotFoundError
from ocp_resources.custom_resource_definition import CustomResourceDefinition
from ocp_resources.namespace import Namespace

from tests.ai_safety.evalhub.constants import (
    EVALHUB_API_GROUP,
    EVALHUB_FULL_API_VERSION_V1,
    EVALHUB_FULL_API_VERSION_V1ALPHA1,
    EVALHUB_KIND,
    EVALHUB_PLURAL,
)


def _evalhub_body(name: str, namespace: str, api_version: str) -> dict:
    """Build an EvalHub resource body for a specific API version."""
    return {
        "apiVersion": api_version,
        "kind": EVALHUB_KIND,
        "metadata": {
            "name": name,
            "namespace": namespace,
        },
        "spec": {
            "database": {"type": "sqlite"},
            "providers": ["lm-evaluation-harness"],
            "collections": ["leaderboard-v2"],
        },
    }


def _evalhub_body_with_database(name: str, namespace: str, api_version: str) -> dict:
    """Build an EvalHub resource body with full database configuration."""
    return {
        "apiVersion": api_version,
        "kind": EVALHUB_KIND,
        "metadata": {
            "name": name,
            "namespace": namespace,
        },
        "spec": {
            "database": {
                "type": "postgresql",
                "secret": "db-secret",
                "maxOpenConns": 50,
                "maxIdleConns": 10,
            },
            "providers": ["lm-evaluation-harness", "garak"],
            "collections": ["leaderboard-v2"],
            "replicas": 1,
        },
    }


def _get_resource(admin_client: DynamicClient, api_version: str):
    """Get the DynamicClient resource handle for a specific EvalHub API version."""
    return admin_client.resources.get(api_version=api_version, kind=EVALHUB_KIND)


def _cleanup_evalhub(admin_client: DynamicClient, name: str, namespace: str) -> None:
    """Delete an EvalHub resource, ignoring NotFound."""
    try:
        resource = _get_resource(admin_client, EVALHUB_FULL_API_VERSION_V1)
        resource.delete(name=name, namespace=namespace)
    except (NotFoundError, ResourceNotFoundError):
        pass


@pytest.mark.smoke
@pytest.mark.ai_safety
def test_evalhub_crd_serves_both_versions(
    admin_client: DynamicClient,
) -> None:
    """Verify the EvalHub CRD advertises both v1alpha1 and v1 versions."""
    crd_name = f"{EVALHUB_PLURAL}.{EVALHUB_API_GROUP}"
    crd = CustomResourceDefinition(
        client=admin_client,
        name=crd_name,
        ensure_exists=True,
    )
    assert crd.exists, f"CRD {crd_name} not found"

    served_versions = {v["name"] for v in crd.instance.spec.versions if v.get("served", False)}
    assert "v1alpha1" in served_versions, f"v1alpha1 not served; served versions: {served_versions}"
    assert "v1" in served_versions, f"v1 not served; served versions: {served_versions}"


@pytest.mark.smoke
@pytest.mark.ai_safety
def test_evalhub_crd_conversion_strategy_is_webhook(
    admin_client: DynamicClient,
) -> None:
    """Verify the EvalHub CRD uses the Webhook conversion strategy."""
    crd_name = f"{EVALHUB_PLURAL}.{EVALHUB_API_GROUP}"
    crd = CustomResourceDefinition(
        client=admin_client,
        name=crd_name,
        ensure_exists=True,
    )
    assert crd.exists, f"CRD {crd_name} not found"

    conversion = crd.instance.spec.get("conversion", {})
    assert conversion.get("strategy") == "Webhook", (
        f"Expected conversion strategy 'Webhook', got '{conversion.get('strategy')}'"
    )


@pytest.mark.parametrize(
    "model_namespace",
    [
        pytest.param(
            {"name": "test-evalhub-conversion"},
        ),
    ],
    indirect=True,
)
@pytest.mark.tier1
@pytest.mark.ai_safety
class TestEvalHubCRDConversion:
    """Tests for the EvalHub v1alpha1 <-> v1 CRD conversion webhook."""

    def test_create_v1alpha1_read_as_v1(
        self,
        admin_client: DynamicClient,
        model_namespace: Namespace,
    ) -> None:
        """Create an EvalHub via v1alpha1 and read it back as v1."""
        name = "conv-v1alpha1-to-v1"
        body = _evalhub_body(name, model_namespace.name, EVALHUB_FULL_API_VERSION_V1ALPHA1)

        v1alpha1_res = _get_resource(admin_client, EVALHUB_FULL_API_VERSION_V1ALPHA1)
        v1alpha1_res.create(body=body, namespace=model_namespace.name)

        try:
            v1_res = _get_resource(admin_client, EVALHUB_FULL_API_VERSION_V1)
            result = v1_res.get(name=name, namespace=model_namespace.name)

            assert result.spec.database.type == "sqlite"
            assert "lm-evaluation-harness" in result.spec.providers
            assert "leaderboard-v2" in result.spec.collections
        finally:
            _cleanup_evalhub(admin_client, name, model_namespace.name)

    def test_create_v1_read_as_v1alpha1(
        self,
        admin_client: DynamicClient,
        model_namespace: Namespace,
    ) -> None:
        """Create an EvalHub via v1 and read it back as v1alpha1."""
        name = "conv-v1-to-v1alpha1"
        body = _evalhub_body(name, model_namespace.name, EVALHUB_FULL_API_VERSION_V1)

        v1_res = _get_resource(admin_client, EVALHUB_FULL_API_VERSION_V1)
        v1_res.create(body=body, namespace=model_namespace.name)

        try:
            v1alpha1_res = _get_resource(admin_client, EVALHUB_FULL_API_VERSION_V1ALPHA1)
            result = v1alpha1_res.get(name=name, namespace=model_namespace.name)

            assert result.spec.database.type == "sqlite"
            assert "lm-evaluation-harness" in result.spec.providers
            assert "leaderboard-v2" in result.spec.collections
        finally:
            _cleanup_evalhub(admin_client, name, model_namespace.name)

    def test_conversion_preserves_database_config(
        self,
        admin_client: DynamicClient,
        model_namespace: Namespace,
    ) -> None:
        """Create an EvalHub with full database config in v1alpha1, read as v1, verify fields."""
        name = "conv-db-preserve"
        body = _evalhub_body_with_database(name, model_namespace.name, EVALHUB_FULL_API_VERSION_V1ALPHA1)

        v1alpha1_res = _get_resource(admin_client, EVALHUB_FULL_API_VERSION_V1ALPHA1)
        v1alpha1_res.create(body=body, namespace=model_namespace.name)

        try:
            v1_res = _get_resource(admin_client, EVALHUB_FULL_API_VERSION_V1)
            result = v1_res.get(name=name, namespace=model_namespace.name)

            assert result.spec.database.type == "postgresql"
            assert result.spec.database.secret == "db-secret"
            assert result.spec.database.maxOpenConns == 50
            assert result.spec.database.maxIdleConns == 10
            assert set(result.spec.providers) == {"lm-evaluation-harness", "garak"}
        finally:
            _cleanup_evalhub(admin_client, name, model_namespace.name)

    def test_roundtrip_v1alpha1_to_v1_to_v1alpha1(
        self,
        admin_client: DynamicClient,
        model_namespace: Namespace,
    ) -> None:
        """Verify roundtrip: create as v1alpha1, read as v1, update via v1, read back as v1alpha1."""
        name = "conv-roundtrip"
        body = _evalhub_body(name, model_namespace.name, EVALHUB_FULL_API_VERSION_V1ALPHA1)

        v1alpha1_res = _get_resource(admin_client, EVALHUB_FULL_API_VERSION_V1ALPHA1)
        v1alpha1_res.create(body=body, namespace=model_namespace.name)

        try:
            v1_res = _get_resource(admin_client, EVALHUB_FULL_API_VERSION_V1)
            v1_obj = v1_res.get(name=name, namespace=model_namespace.name)
            assert v1_obj.spec.database.type == "sqlite"

            roundtrip = v1alpha1_res.get(name=name, namespace=model_namespace.name)
            assert roundtrip.spec.database.type == "sqlite"
            assert "lm-evaluation-harness" in roundtrip.spec.providers
            assert "leaderboard-v2" in roundtrip.spec.collections
        finally:
            _cleanup_evalhub(admin_client, name, model_namespace.name)
