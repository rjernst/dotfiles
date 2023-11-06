
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
  echo "Setting up $1 support environment"
  if [[ $1 == prod ]]; then
    ENV_URL=https://global.elastic.cloud
    TSH_PROXY=teleport-proxy.secops.elstc.co
    ENV_LINKS="
    Cloud UI: https://cloud.elastic.co/
    Admin UI: https://admin.found.no/projects/
    Observability overview: https://overview.elastic-cloud.com/"
  fi

  API_KEY=$(security find-generic-password -a $USER -s admin-console-api-key-$ENV_NAME -w 2> /dev/null)
  if [ $? -ne 0 ]; then
    echo "API key is not set for $ENV_NAME. Use 'set-env $ENV_NAME' to set it."
    return 3
  fi
  ping -c 1 $TSH_PROXY &> /dev/null
  if [ $? -ne 0 ]; then
    echo "Cannot reach tsh proxy at $TSH_PROXY. Are you logged into the VPN?"
    return 4
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
  security add-generic-password -a $USER -s admin-console-api-key-$ENV_NAME -w
}
alias set-api-key='_set_api_key'


function _set_project() {
  if [[ ! -v SOME_VARIABLE ]]; then
    echo "Must first run 'setup-support ENV' before setting project id"
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
