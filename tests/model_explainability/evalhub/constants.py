EVALHUB_SERVICE_NAME: str = "evalhub"
EVALHUB_SERVICE_PORT: int = 8443
EVALHUB_CONTAINER_PORT: int = 8080
EVALHUB_HEALTH_PATH: str = "/api/v1/health"
EVALHUB_PROVIDERS_PATH: str = "/api/v1/evaluations/providers"
EVALHUB_JOBS_PATH: str = "/api/v1/evaluations/jobs"
EVALHUB_HEALTH_STATUS_HEALTHY: str = "healthy"

EVALHUB_APP_LABEL: str = "eval-hub"

# CRD details
EVALHUB_API_GROUP: str = "trustyai.opendatahub.io"
EVALHUB_API_VERSION: str = "v1alpha1"
EVALHUB_KIND: str = "EvalHub"
EVALHUB_PLURAL: str = "evalhubs"

# RBAC ClusterRole names (must match operator config/rbac/evalhub/ YAML files)
EVALHUB_PROVIDERS_ACCESS_CLUSTER_ROLE: str = "trustyai-service-operator-evalhub-providers-access"

# Test provider
EVALHUB_TEST_PROVIDER_ID: str = "test"
EVALHUB_TEST_PROVIDER_CONFIGMAP_NAME: str = "evalhub-provider-test"
EVALHUB_TEST_BENCHMARK_ID: str = "datafile"

# Provider ConfigMap labels (must match what the operator watches for)
EVALHUB_PROVIDER_TYPE_LABEL: str = "trustyai.opendatahub.io/evalhub-provider-type"
EVALHUB_PROVIDER_NAME_LABEL: str = "trustyai.opendatahub.io/evalhub-provider-name"
