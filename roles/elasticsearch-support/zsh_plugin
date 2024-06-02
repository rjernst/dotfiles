
function _print_support_help() {
  echo "Commands:"
  echo "  set-env     - set env variables and log into tsh for an environment"
  echo "  set-project - set a project to investigate in the current environment"
  echo "  set-api-key - set the console api key for an environment"
  echo "  links       - print relevant web links for the current environment"
}
alias help=_print_support_help

function _set_env() {
  envs=(prod staging qa)
  if [ $# -ne 1 ]; then
      echo "Expected a single env parameter from [$envs]"
      return 1
  fi
  ENV_NAME=$1
  if [[ $envs[(Ie)$ENV_NAME] -eq 0 ]]; then
    echo "Illegal env name, should be one of [$envs]"
    return 2
  fi
  echo "Setting up $ENV_NAME support environment"
  if [[ $ENV_NAME == prod ]]; then
    TSH_PROXY=teleport-proxy.secops.elstc.co
    ADMIN_UI=https://overview.elastic-cloud.com/
    ENV_URL=https://admin.global.elastic.cloud
    ENV_LINKS="
    Cloud UI:      https://cloud.elastic.co/
    Admin UI:      https://admin.found.no/projects/
    Observability: https://overview.elastic-cloud.com/
    Logs:          https://overview.elastic-cloud.com/app/discover#/view/bd378440-4e26-11ee-9def-2f88d8044fe2
    "
  fi
  if [[ $ENV_NAME == staging ]]; then
    TSH_PROXY=teleport-proxy.staging.getin.cloud
    ADMIN_UI=https://admin.staging.foundit.no
    ENV_URL=https://admin.global.staging.cld.elstc.co
    ENV_LINKS="
    Cloud UI:      https://staging.found.no/
    Admin UI:      https://admin.staging.foundit.no/
    Observability: https://overview.aws.staging.foundit.no/
    Logs:          https://overview.aws.staging.foundit.no/app/discover#/view/b3e75f40-4e27-11ee-9d5f-69c5746c998f
    "
  fi
  if [[ $ENV_NAME == qa ]]; then
    TSH_PROXY=teleport-proxy.staging.getin.cloud
    ADMIN_UI=https://admin.qa.cld.elstc.co
    ENV_URL=https://admin.global.qa.cld.elstc.co
    ENV_LINKS="
    Cloud UI:      https://console.qa.cld.elstc.co/
    Admin UI:      https://admin.qa.cld.elstc.co/
    Observability: https://overview.qa.cld.elstc.co/
    Logs:          https://overview.qa.cld.elstc.co/app/discover#/view/df2a5ec0-4e20-11ee-889b-75400a7667fb?_g=h@2294574&_a=h@38de511
    "
  fi

  API_KEY=$(security find-generic-password -a $USER -s admin-console-api-key-$ENV_NAME -w 2> /dev/null)
  if [ $? -ne 0 ]; then
    echo "API key is not set for $ENV_NAME. Use 'set-api-key $ENV_NAME' to set it."
    echo "Create a key at $ADMIN_UI/keys"
    return 3
  fi
  curl -s "${ENV_URL}/api/v1/admin/serverless/projects/elasticsearch" -f -H "Authorization: ApiKey $API_KEY" >/dev/null || echo "fail"
  if [ $? -ne 0 ]; then
    echo "API key is not valid for $ENV_NAME. Use 'set-api-key $ENV_NAME' to reset it."
    echo "Create a key at $ADMIN_UI/keys"
    return 4
  fi

  ping -c 1 $TSH_PROXY &> /dev/null
  if [ $? -ne 0 ]; then
    echo "Cannot reach tsh proxy at $TSH_PROXY. Are you logged into the VPN?"
    return 5
  fi
  echo "Logging into tsh at $TSH_PROXY"
  tsh login --proxy=$TSH_PROXY --auth=okta

  echo "Using ENV_URL: $ENV_URL"
  echo "Using TSH_PROXY: $TSH_PROXY"
  export ENV_NAME
  export ENV_URL
  export TSH_PROXY
  export API_KEY
  export ENV_LINKS

  alias links='echo $ENV_LINKS'
}
alias set-env='_set_env'

function _set_api_key() {
  if [ $# -ne 1 ]; then
      echo "Expected a single ENV parameter"
  fi
  ENV_NAME=$1
  security add-generic-password -a $USER -s admin-console-api-key-$ENV_NAME -U -w
}
alias set-api-key='_set_api_key'


function _set_project() {
  if [[ ! -v ENV_NAME ]]; then
    echo "Must first run 'set-env prod|staging|qa' before setting project id"
    return
  fi
  if [ $# -ne 1 ]; then
      echo "Expected a single PROJECT_ID parameter"
  fi
  
  export PROJECT_ID=$1
  for P in elasticsearch observability security; do
    export PROJECT_TYPE=$P
    curl -s "$ENV_URL/api/v1/admin/serverless/projects/$PROJECT_TYPE/$PROJECT_ID" --silent -f -H "Authorization: ApiKey $API_KEY" > /dev/null && break
  done
  echo "$PROJECT_ID is a $PROJECT_TYPE project"

  echo "Running curl 'GET _cat/allocation' request to check setup..."
  curl -H "Authorization: ApiKey $API_KEY" -H "Content-Type: application/json" "$ENV_URL/api/v1/admin/serverless/projects/$PROJECT_TYPE/$PROJECT_ID/_proxy/_cat/allocation?v"

  K8S_CLUSTER_NAME=$(curl "$ENV_URL/api/v1/admin/serverless/projects/$PROJECT_TYPE/$PROJECT_ID" --silent -f -H "Authorization: ApiKey $API_KEY" | jq -cMr .clusters.elasticsearch)
  echo "Using K8S_CLUSTER_NAME: $K8S_CLUSTER_NAME"

  echo "Logging into $K8S_CLUSTER_NAME kubernetes cluster.."
  tsh kube login $K8S_CLUSTER_NAME

  echo "Listing pods..."
  kubectl get pods -n project-$PROJECT_ID
}
alias set-project='_set_project'

function _get_build_info() {
  if [[ ! -v ENV_NAME ]]; then
    echo "Must first run 'set-env prod|staging|qa' before getting version info"
    return
  fi
  if [[ ! -v PROJECT_ID ]]; then
    echo "Must first run 'set-project PROJECT_ID' before getting version info"
    return
  fi

  curl -s -H "Authorization: ApiKey $API_KEY" -H "Content-Type: application/json" "$ENV_URL/api/v1/admin/serverless/projects/$PROJECT_TYPE/$PROJECT_ID/_proxy/_nodes" | jq -cMr '["Node","Serverless Build Hash"], (.nodes | keys[] as $key | .[$key] | [.name, .build_hash]) | @tsv' | column -t -s $'\t' 
}
alias get-build-info='_get_build_info'
