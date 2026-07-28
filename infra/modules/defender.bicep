// ---------------------------------------------------------------------------
// Microsoft Defender for Cloud plans module (SUBSCRIPTION SCOPE)
//
// Microsoft.Security/pricings is a subscription-level resource. It CANNOT be
// deployed from a resource-group-scoped deployment, so this module is NOT wired
// into main.bicep. Deploy it on its own with:
//
//   az deployment sub create \
//     --location eastus2 \
//     --template-file infra/modules/defender.bicep
//
// It enables the six Standard plans currently active on the subscription:
//   SqlServers, AppServices, KeyVaults (PerKeyVault), OpenSourceRelationalDatabases,
//   Discovery, FoundationalCspm.
// ---------------------------------------------------------------------------

targetScope = 'subscription'

@description('Defender plans to enable at Standard. name = plan, subPlan optional.')
param standardPlans array = [
  { name: 'SqlServers', subPlan: '' }
  { name: 'AppServices', subPlan: '' }
  { name: 'KeyVaults', subPlan: 'PerKeyVault' }
  { name: 'OpenSourceRelationalDatabases', subPlan: '' }
  { name: 'Discovery', subPlan: '' }
  { name: 'FoundationalCspm', subPlan: '' }
]

resource pricings 'Microsoft.Security/pricings@2024-01-01' = [for plan in standardPlans: {
  name: plan.name
  properties: empty(plan.subPlan) ? {
    pricingTier: 'Standard'
  } : {
    pricingTier: 'Standard'
    subPlan: plan.subPlan
  }
}]
