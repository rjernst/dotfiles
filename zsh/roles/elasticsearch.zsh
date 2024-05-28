
# for windows packaging tests
export VAGRANT_WINDOWS_2012R2_BOX=elastic/windows-2012_r2-x86_64

# vault address
export VAULT_ADDR=https://secrets.elastic.co:8200

alias vault-login='vault login -method github token="$(<~/.elastic/github.token)"'
alias okta-login='okta-awscli --profile=okta-elastic-dev'

# aws access
alias aws='okta-awscli --profile=okta-elastic-dev'
export AWS_PROFILE=okta-elastic-dev

# shortcuts to projects
alias cde='cd ~/code/elastic/elasticsearch'
alias cdes='cd ~/code/elastic/elasticsearch-serverless'
alias cde7='cd ~/code/elastic/elasticsearch-7.x'
alias cde6='cd ~/code/elastic/elasticsearch-6.x'
alias cdb='cd ~/code/elastic/elasticsearch-benchmarks'

function _reload_ssh_keys() {
  if [[ $(uname) == "Darwin" ]]; then
    SSH_ADD_OPTIONS=--apple-use-keychain --apple-load-keychain
  fi
  keys=('id_ed25519' 'github_ed25519')
  for key in $keys; do
    ssh-add $SSH_ADD_OPTIONS ~/.ssh/$key
  done
}
alias reload-ssh-keys='_reload_ssh_keys'
