// ---------------------------------------------------------------------------
// Key Vault module
// Recreates docuaction-kv-prod: RBAC-authorized, soft-delete + purge-protection,
// 90-day soft-delete retention, standard SKU. Optionally seeds the secrets that
// the App Service references via @Microsoft.KeyVault(...) app settings.
// ---------------------------------------------------------------------------

@description('Key Vault name (globally unique).')
param keyVaultName string

@description('Azure region for the vault.')
param location string

@description('Entra ID tenant GUID that governs the vault.')
param tenantId string = subscription().tenantId

@description('Soft-delete retention window in days (7-90).')
@minValue(7)
@maxValue(90)
param softDeleteRetentionInDays int = 90

@description('Public network access to the data plane.')
@allowed([
  'Enabled'
  'Disabled'
])
param publicNetworkAccess string = 'Enabled'

@description('Create placeholder secrets that the App Service references. Set false if secrets are managed out-of-band.')
param createSecrets bool = true

@description('SECRET_KEY value (>=64 chars in prod). Placeholder only in params files.')
@secure()
param secretKey string = ''

@description('ANTHROPIC_API_KEY value.')
@secure()
param anthropicApiKey string = ''

@description('AZURE_AD_CLIENT_SECRET value.')
@secure()
param azureAdClientSecret string = ''

@description('SENDGRID_API_KEY value.')
@secure()
param sendGridApiKey string = ''

@description('Tags applied to the vault.')
param tags object = {}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    tenantId: tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableSoftDelete: true
    softDeleteRetentionInDays: softDeleteRetentionInDays
    enablePurgeProtection: true
    enableRbacAuthorization: true
    enabledForDeployment: false
    enabledForDiskEncryption: false
    enabledForTemplateDeployment: false
    publicNetworkAccess: publicNetworkAccess
  }
}

// Secret names use hyphens (KV constraint); app settings map them to underscored env vars.
var secretDefs = [
  { name: 'SECRET-KEY', value: secretKey }
  { name: 'ANTHROPIC-API-KEY', value: anthropicApiKey }
  { name: 'AZURE-AD-CLIENT-SECRET', value: azureAdClientSecret }
  { name: 'SENDGRID-API-KEY', value: sendGridApiKey }
]

resource secrets 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = [for s in secretDefs: if (createSecrets) {
  parent: keyVault
  name: s.name
  properties: {
    value: s.value
  }
}]

output keyVaultId string = keyVault.id
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
