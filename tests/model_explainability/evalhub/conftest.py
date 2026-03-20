import shlex
from collections.abc import Generator
from typing import Any

import pytest
from kubernetes.dynamic import DynamicClient
from ocp_resources.cluster_role import ClusterRole
from ocp_resources.config_map import ConfigMap
from ocp_resources.deployment import Deployment
from ocp_resources.namespace import Namespace
from ocp_resources.role_binding import RoleBinding
from ocp_resources.route import Route
from ocp_resources.service_account import ServiceAccount
from pyhelper_utils.shell import run_command
from simple_logger.logger import get_logger
from timeout_sampler import TimeoutSampler

from tests.model_explainability.evalhub.constants import (
    EVALHUB_PROVIDER_NAME_LABEL,
    EVALHUB_PROVIDER_TYPE_LABEL,
    EVALHUB_PROVIDERS_ACCESS_CLUSTER_ROLE,
    EVALHUB_TEST_BENCHMARK_ID,
    EVALHUB_TEST_PROVIDER_CONFIGMAP_NAME,
    EVALHUB_TEST_PROVIDER_ID,
)
from tests.model_explainability.evalhub.utils import get_evalhub_provider, list_evalhub_providers
from utilities.certificates_utils import create_ca_bundle_file
from utilities.constants import Timeout
from utilities.infra import create_ns
from utilities.resources.evalhub import EvalHub

LOGGER = get_logger(name=__name__)

# Label that marks a namespace as an EvalHub tenant namespace (must match the operator).
EVALHUB_TENANT_LABEL: str = "evalhub.trustyai.opendatahub.io/tenant"


# ---------------------------------------------------------------------------
# Namespace fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def evalhub_control_plane_namespace(
    admin_client: DynamicClient,
    teardown_resources: bool,
) -> Generator[Namespace, Any, Any]:
    """Control plane namespace where the EvalHub CR is deployed."""
    with create_ns(
        name="test-evalhub-control-plane",
        admin_client=admin_client,
        teardown=teardown_resources,
    ) as ns:
        yield ns


@pytest.fixture(scope="session")
def evalhub_team_a_namespace(
    admin_client: DynamicClient,
    teardown_resources: bool,
) -> Generator[Namespace, Any, Any]:
    """Tenant namespace for team-a, labelled as an EvalHub tenant (authorised)."""
    with create_ns(
        name="test-evalhub-team-a",
        admin_client=admin_client,
        labels={EVALHUB_TENANT_LABEL: ""},
        teardown=teardown_resources,
    ) as ns:
        yield ns


@pytest.fixture(scope="session")
def evalhub_team_b_namespace(
    admin_client: DynamicClient,
    teardown_resources: bool,
) -> Generator[Namespace, Any, Any]:
    """Tenant namespace for team-b, labelled as an EvalHub tenant but without providers RBAC."""
    with create_ns(
        name="test-evalhub-team-b",
        admin_client=admin_client,
        labels={EVALHUB_TENANT_LABEL: ""},
        teardown=teardown_resources,
    ) as ns:
        yield ns


@pytest.fixture(scope="session")
def evalhub_team_c_namespace(
    admin_client: DynamicClient,
    teardown_resources: bool,
) -> Generator[Namespace, Any, Any]:
    """Non-tenant namespace for team-c - NOT labelled as an EvalHub tenant."""
    with create_ns(
        name="test-evalhub-team-c",
        admin_client=admin_client,
        teardown=teardown_resources,
    ) as ns:
        yield ns


# ---------------------------------------------------------------------------
# EvalHub infrastructure fixtures (session-scoped, shared across all classes)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def evalhub_cr(
    admin_client: DynamicClient,
    evalhub_control_plane_namespace: Namespace,
) -> Generator[EvalHub, Any, Any]:
    """Create an EvalHub custom resource in the control plane namespace and wait for it to be ready."""
    with EvalHub(
        client=admin_client,
        name="evalhub",
        namespace=evalhub_control_plane_namespace.name,
        providers=["garak", "garak-kfp", "guidellm", "lighteval", "lm-evaluation-harness", "ragas"],
        wait_for_resource=True,
    ) as evalhub:
        yield evalhub


@pytest.fixture(scope="session")
def evalhub_deployment(
    admin_client: DynamicClient,
    evalhub_control_plane_namespace: Namespace,
    evalhub_cr: EvalHub,
) -> Deployment:
    """Wait for the EvalHub deployment in the control plane namespace to become available."""
    deployment = Deployment(
        client=admin_client,
        name=evalhub_cr.name,
        namespace=evalhub_control_plane_namespace.name,
    )
    deployment.wait_for_replicas(timeout=Timeout.TIMEOUT_5MIN)
    return deployment


@pytest.fixture(scope="session")
def evalhub_route(
    admin_client: DynamicClient,
    evalhub_control_plane_namespace: Namespace,
    evalhub_deployment: Deployment,
) -> Route:
    """Get the Route created by the operator for the EvalHub service."""
    return Route(
        client=admin_client,
        name=evalhub_deployment.name,
        namespace=evalhub_control_plane_namespace.name,
        ensure_exists=True,
    )


@pytest.fixture(scope="class")
def evalhub_ca_bundle_file(
    admin_client: DynamicClient,
) -> str:
    """Create a CA bundle file for verifying the EvalHub route TLS certificate."""
    return create_ca_bundle_file(client=admin_client)


# ---------------------------------------------------------------------------
# team-a (labelled tenant, authorised) fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="class")
def evalhub_scoped_sa(
    admin_client: DynamicClient,
    evalhub_team_a_namespace: Namespace,
    evalhub_deployment: Deployment,
) -> Generator[ServiceAccount, Any, Any]:
    """ServiceAccount with providers access in the team-a tenant namespace."""
    with ServiceAccount(
        client=admin_client,
        name="evalhub-test-user",
        namespace=evalhub_team_a_namespace.name,
    ) as sa:
        yield sa


@pytest.fixture(scope="class")
def evalhub_providers_role_binding(
    admin_client: DynamicClient,
    evalhub_team_a_namespace: Namespace,
    evalhub_scoped_sa: ServiceAccount,
) -> Generator[RoleBinding, Any, Any]:
    """RoleBinding granting the scoped SA providers access via the ClusterRole in team-a."""
    with RoleBinding(
        client=admin_client,
        name="evalhub-test-providers-access",
        namespace=evalhub_team_a_namespace.name,
        role_ref_kind="ClusterRole",
        role_ref_name=EVALHUB_PROVIDERS_ACCESS_CLUSTER_ROLE,
        subjects_kind="ServiceAccount",
        subjects_name=evalhub_scoped_sa.name,
    ) as rb:
        yield rb


@pytest.fixture(scope="class")
def evalhub_scoped_token(
    evalhub_scoped_sa: ServiceAccount,
    evalhub_team_a_namespace: Namespace,
) -> str:
    """Short-lived token for the scoped ServiceAccount in team-a."""
    return run_command(
        command=shlex.split(
            f"oc create token -n {evalhub_team_a_namespace.name} {evalhub_scoped_sa.name} --duration=30m"
        )
    )[1].strip()


@pytest.fixture(scope="class")
def evalhub_providers_response(
    evalhub_team_a_namespace: Namespace,
    evalhub_scoped_token: str,
    evalhub_providers_role_binding: RoleBinding,
    evalhub_ca_bundle_file: str,
    evalhub_route: Route,
) -> dict:
    """Fetch the providers list once per test class using the team-a tenant."""
    return list_evalhub_providers(
        host=evalhub_route.host,
        token=evalhub_scoped_token,
        ca_bundle_file=evalhub_ca_bundle_file,
        tenant=evalhub_team_a_namespace.name,
    )


# ---------------------------------------------------------------------------
# team-b (labelled tenant, no RBAC) fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="class")
def evalhub_unauthorised_sa(
    admin_client: DynamicClient,
    evalhub_team_b_namespace: Namespace,
    evalhub_deployment: Deployment,
) -> Generator[ServiceAccount, Any, Any]:
    """ServiceAccount without providers RBAC in the team-b tenant namespace."""
    with ServiceAccount(
        client=admin_client,
        name="evalhub-no-access-user",
        namespace=evalhub_team_b_namespace.name,
    ) as sa:
        yield sa


@pytest.fixture(scope="class")
def evalhub_unauthorised_token(
    evalhub_unauthorised_sa: ServiceAccount,
    evalhub_team_b_namespace: Namespace,
) -> str:
    """Short-lived token for the unauthorised ServiceAccount in team-b."""
    return run_command(
        command=shlex.split(
            f"oc create token -n {evalhub_team_b_namespace.name} {evalhub_unauthorised_sa.name} --duration=30m"
        )
    )[1].strip()


# ---------------------------------------------------------------------------
# team-c (non-tenant) fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="class")
def evalhub_non_tenant_sa(
    admin_client: DynamicClient,
    evalhub_team_c_namespace: Namespace,
    evalhub_deployment: Deployment,
) -> Generator[ServiceAccount, Any, Any]:
    """ServiceAccount in the team-c non-tenant namespace."""
    with ServiceAccount(
        client=admin_client,
        name="evalhub-non-tenant-user",
        namespace=evalhub_team_c_namespace.name,
    ) as sa:
        yield sa


@pytest.fixture(scope="class")
def evalhub_non_tenant_token(
    evalhub_non_tenant_sa: ServiceAccount,
    evalhub_team_c_namespace: Namespace,
) -> str:
    """Short-lived token for the ServiceAccount in team-c."""
    return run_command(
        command=shlex.split(
            f"oc create token -n {evalhub_team_c_namespace.name} {evalhub_non_tenant_sa.name} --duration=30m"
        )
    )[1].strip()


# ---------------------------------------------------------------------------
# Test provider fixtures (session-scoped, shared across job tests)
# ---------------------------------------------------------------------------

_TEST_PROVIDER_YAML = """\
id: test
name: Test Provider
type: builtin
runtime:
  k8s:
    image: image-registry.openshift-image-registry.svc:5000/image-builds/community-test-provider:latest
    entrypoint: [python, -m, evalhub_test_provider.adapter]
    cpu_request: 100m
    memory_request: 128Mi
    cpu_limit: 500m
    memory_limit: 1Gi
  local: null
benchmarks:
- id: datafile
  name: Datafile Eval
  category: general
  metrics: [row_count, column_count]
"""


@pytest.fixture(scope="session")
def evalhub_test_provider_configmap(
    admin_client: DynamicClient,
    evalhub_control_plane_namespace: Namespace,
    evalhub_deployment: Deployment,
    teardown_resources: bool,
) -> Generator[ConfigMap, Any, Any]:
    """ConfigMap in the control plane namespace that registers the test provider with EvalHub."""
    with ConfigMap(
        client=admin_client,
        name=EVALHUB_TEST_PROVIDER_CONFIGMAP_NAME,
        namespace=evalhub_control_plane_namespace.name,
        label={
            EVALHUB_PROVIDER_TYPE_LABEL: "system",
            EVALHUB_PROVIDER_NAME_LABEL: EVALHUB_TEST_PROVIDER_ID,
        },
        data={"test.yaml": _TEST_PROVIDER_YAML},
        teardown=teardown_resources,
    ) as cm:
        yield cm


@pytest.fixture(scope="class")
def evalhub_test_provider_ready(
    evalhub_test_provider_configmap: ConfigMap,
    evalhub_route: Route,
    evalhub_scoped_token: str,
    evalhub_ca_bundle_file: str,
    evalhub_team_a_namespace: Namespace,
    evalhub_providers_role_binding: RoleBinding,
) -> None:
    """Wait until the test provider appears in the EvalHub providers list."""
    for providers_data in TimeoutSampler(
        wait_timeout=Timeout.TIMEOUT_2MIN,
        sleep=5,
        func=list_evalhub_providers,
        host=evalhub_route.host,
        token=evalhub_scoped_token,
        ca_bundle_file=evalhub_ca_bundle_file,
        tenant=evalhub_team_a_namespace.name,
    ):
        provider_ids = [p["resource"]["id"] for p in providers_data.get("items", [])]
        LOGGER.info(f"Waiting for test provider. Current providers: {provider_ids}")
        if EVALHUB_TEST_PROVIDER_ID in provider_ids:
            LOGGER.info("Test provider is ready")
            return


# ---------------------------------------------------------------------------
# Evaluations RBAC fixtures (session-scoped ClusterRole, class-scoped binding)
# ---------------------------------------------------------------------------

#: Name of the ClusterRole that grants the right to create evaluation jobs.
EVALHUB_EVALUATIONS_ACCESS_CLUSTER_ROLE: str = "evalhub-test-evaluations-access"


@pytest.fixture(scope="session")
def evalhub_evaluations_cluster_role(
    admin_client: DynamicClient,
    teardown_resources: bool,
) -> Generator[ClusterRole, Any, Any]:
    """ClusterRole granting the permissions required to submit EvalHub jobs."""
    with ClusterRole(
        client=admin_client,
        name=EVALHUB_EVALUATIONS_ACCESS_CLUSTER_ROLE,
        rules=[
            {
                "apiGroups": ["trustyai.opendatahub.io"],
                "resources": ["evaluations"],
                "verbs": ["create", "get", "list", "delete"],
            },
            {
                "apiGroups": ["mlflow.kubeflow.org"],
                "resources": ["experiments"],
                "verbs": ["create", "get"],
            },
        ],
        teardown=teardown_resources,
    ) as cr:
        yield cr


@pytest.fixture(scope="class")
def evalhub_evaluations_role_binding(
    admin_client: DynamicClient,
    evalhub_team_a_namespace: Namespace,
    evalhub_scoped_sa: ServiceAccount,
    evalhub_evaluations_cluster_role: ClusterRole,
) -> Generator[RoleBinding, Any, Any]:
    """RoleBinding in team-a that grants the scoped SA permission to submit evaluation jobs."""
    with RoleBinding(
        client=admin_client,
        name="evalhub-test-evaluations-access",
        namespace=evalhub_team_a_namespace.name,
        role_ref_kind="ClusterRole",
        role_ref_name=EVALHUB_EVALUATIONS_ACCESS_CLUSTER_ROLE,
        subjects_kind="ServiceAccount",
        subjects_name=evalhub_scoped_sa.name,
    ) as rb:
        yield rb
