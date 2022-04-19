
# for windows packaging tests
export VAGRANT_WINDOWS_2012R2_BOX=elastic/windows-2012_r2-x86_64

# vault address
export VAULT_ADDR=https://secrets.elastic.co:8200

alias vault-login='vault login -method github token="$(<~/.elastic/vault.gh.token)"'

# aws access
alias aws='okta-awscli --profile=okta-elastic-dev'
export AWS_PROFILE=okta-elastic-dev

# shortcuts to projects
alias cde='cd ~/code/elastic/elasticsearch'
alias cde7='cd ~/code/elastic/elasticsearch-7.x'
alias cde6='cd ~/code/elastic/elasticsearch-6.x'
alias cdb='cd ~/code/elastic/elasticsearch-benchmarks'
