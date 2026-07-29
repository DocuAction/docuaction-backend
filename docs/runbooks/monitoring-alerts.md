# Runbook: monitoring and alert rules

**Status:** partially in place. App Insights is attached to prod
(`docuaction-appinsights`, visible as a hidden-link tag on the site). Alert rules
are the gap.

## What to alert on, and why these

The list is short on purpose. An alert nobody acts on trains people to ignore the
channel.

| Alert | Condition | Why |
|---|---|---|
| Availability | `/health` non-200 for 5 min | The single signal that the app is down. Everything else is diagnosis. |
| HTTP 5xx rate | > 10 in 5 min | Catches a bad deploy that still answers `/health`. |
| Response time | P95 > 5s for 15 min | The bulletin run and TEFCA connector probes are the usual causes. |
| CPU | > 80% for 15 min | S1 is a single small instance; CPU saturation precedes timeouts. |
| Deployment failure | `az webapp deploy` non-success | See the caveat below. |

## Availability test

```bash
az monitor app-insights web-test create \
  --resource-group rg-docuaction-prod \
  --name docuaction-prod-health \
  --location eastus2 \
  --web-test-kind standard \
  --request-url https://api-prod.docuaction.io/health \
  --frequency 300 \
  --expected-status-code 200
```

## Metric alert example

```bash
az monitor metrics alert create \
  --name docuaction-prod-5xx \
  --resource-group rg-docuaction-prod \
  --scopes $(az webapp show -n Docuaction -g rg-docuaction-prod --query id -o tsv) \
  --condition "total Http5xx > 10" \
  --window-size 5m --evaluation-frequency 1m \
  --action <ACTION_GROUP_ID>
```

Create the action group first; an alert with no action group fires into nothing:
```bash
az monitor action-group create -n docuaction-oncall -g rg-docuaction-prod \
  --short-name dpc-oncall --action email primary imran@agtbi.com
```

## Caveat on deployment alerts

`az webapp deploy` **returns a connection error while the deployment is still
succeeding** — the CLI drops its polling connection and prints a failure banner.
An alert keyed on CLI exit status will produce false alarms. Key it on the
server-side record instead:

```bash
az webapp log deployment list -n Docuaction -g rg-docuaction-prod \
  --query "[0].{status:status,active:active}" -o tsv
```

Status `4` with `active: True` is a real success. `3` is a real failure.

## Verify

Alerts are only real once one has fired. After creating them, confirm delivery
end-to-end rather than assuming — an untested alert rule is a documented
intention, not a control.
