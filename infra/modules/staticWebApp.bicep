// ---------------------------------------------------------------------------
// Static Web App module
// Recreates docuaction-frontend (Free tier). The prod site is deployed via the
// SWA CLI (provider = SwaCli) with no linked GitHub repo, so repositoryUrl /
// branch are intentionally left unset. Custom domains (app.docuaction.io) are
// managed as child staticSites/customDomains resources out of band.
// ---------------------------------------------------------------------------

@description('Static Web App name.')
param staticWebAppName string

@description('Azure region (must be a SWA-supported region, e.g. eastus2).')
param location string

@description('SKU name/tier.')
@allowed([
  'Free'
  'Standard'
])
param skuName string = 'Free'

@description('Allow the SWA config file to update platform settings.')
param allowConfigFileUpdates bool = true

@description('Preview/staging environment policy.')
@allowed([
  'Enabled'
  'Disabled'
])
param stagingEnvironmentPolicy string = 'Enabled'

@description('Tags applied to the Static Web App.')
param tags object = {}

resource staticWebApp 'Microsoft.Web/staticSites@2024-04-01' = {
  name: staticWebAppName
  location: location
  tags: tags
  sku: {
    name: skuName
    tier: skuName
  }
  properties: {
    allowConfigFileUpdates: allowConfigFileUpdates
    stagingEnvironmentPolicy: stagingEnvironmentPolicy
    enterpriseGradeCdnStatus: 'Disabled'
  }
}

output staticWebAppId string = staticWebApp.id
output staticWebAppName string = staticWebApp.name
output defaultHostname string = staticWebApp.properties.defaultHostname
