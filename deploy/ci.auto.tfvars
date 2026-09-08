# Stable baseline for every apply, human or CI. Auto-loaded by Terraform with no flag
# needed (the .auto.tfvars suffix), and the one committed exception to this directory's
# own *.auto.tfvars gitignore rule, since nothing here is secret or environment-specific.
#
# create_role_assignments: the four role assignments this deployment needs already
# exist, granted out of band by an admin with Owner or User Access Administrator (see
# deploy/README.md, "When role assignments fail"). Neither a human running this locally
# nor the CI service principal holds that permission, so this stays false rather than
# failing every apply on an authorization error.
#
# acr_pull_confirmed: the grant above already covers AcrPull, so this attests that
# rather than re-confirming it by hand on every single apply. See variables.tf for the
# precondition this satisfies and why it exists at all.
create_role_assignments = false
acr_pull_confirmed      = true
