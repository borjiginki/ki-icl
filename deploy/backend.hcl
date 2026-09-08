# Non-secret backend config: which storage account and blob hold the state.
# The access key itself is never written here - it comes from the ARM_ACCESS_KEY
# env var at init time, the same "key stays out of git" pattern as the audit key
# and the Files share key. See deploy/README.md, "Remote state".
resource_group_name  = "ki-icl-tfstate"
storage_account_name = "kiicltfstateaec301"
container_name       = "tfstate"
key                  = "ki-icl.tfstate"
