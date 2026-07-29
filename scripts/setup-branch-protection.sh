#!/bin/bash
# Run once to enable branch protection on main for both repos.
# Requires: gh cli authenticated with admin rights on DocuAction/*
#
# NOTE ON --field: gh's --field only coerces true/false/numbers/null. A nested
# JSON object passed that way is sent as a literal string and the API rejects it
# with 422 Invalid request. Protection rules must therefore go in as a real JSON
# body via --input, which is what this script does.

set -euo pipefail

protect() {
  local repo="$1" check="$2"
  echo "Setting up branch protection for ${repo}/main (required check: ${check})..."
  gh api "repos/${repo}/branches/main/protection" \
    --method PUT \
    --input - <<JSON
{
  "required_status_checks": { "strict": true, "contexts": ["${check}"] },
  "enforce_admins": false,
  "required_pull_request_reviews": { "required_approving_review_count": 1 },
  "restrictions": null
}
JSON
  echo "  ${repo} done."
}

# The context string must match the JOB id in the workflow, not the workflow name.
protect "DocuAction/docuaction-backend"  "build-and-test"
protect "DocuAction/docuaction-frontend" "build"

echo
echo "Done. PRs now require 1 reviewer + CI pass."
echo
echo "Verify with:"
echo "  gh api repos/DocuAction/docuaction-backend/branches/main/protection --jq '.required_status_checks'"
echo
echo "Caveat: enforce_admins=false means an admin can still push straight to main."
echo "That is deliberate here so a hotfix path exists, but it means this is a"
echo "guardrail, not a control you can cite as enforced in an audit."
