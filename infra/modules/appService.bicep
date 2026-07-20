// ---------------------------------------------------------------------------
// App Service module
// Recreates the ASP-rgdocuactionprod P0v3 Linux plan + Docuaction Web App:
// Python 3.12, gunicorn/uvicorn command line, system-assigned managed identity,
// /health health check, HTTPS-only, TLS 1.2, and app settings including
// Key Vault reference placeholders resolved through the managed identity.
// Optionally grants the site identity Key Vault Secrets User on the vault.
// ---------------------------------------------------------------------------

@description('App Service Plan name.')
param appServicePlanName string

@description('Web App name (globally unique).')
param webAppName string

@description('Azure region.')
param location string

@description('Plan SKU name.')
param skuName string = 'P0v3'

@description('Plan SKU tier.')
param skuTier string = 'Premium0V3'

@description('Plan instance count.')
param skuCapacity int = 1

@description('Linux runtime stack.')
param linuxFxVersion string = 'PYTHON|3.12'

@description('Startup command.')
param appCommandLine string = 'python -m gunicorn app.main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000'

@description('Health check path.')
param healthCheckPath string = '/health'

@description('Key Vault name whose secrets are referenced by app settings.')
param keyVaultName string

@description('Application Insights connection string.')
param appInsightsConnectionString string = ''

@description('Application Insights instrumentation key.')
param appInsightsInstrumentationKey string = ''

@description('Public application URL (frontend).')
param appUrl string = 'https://app.docuaction.io'

@description('Comma-separated ALLOWED_HOSTS.')
param allowedHosts string = '*.azurewebsites.net,api.docuaction.io,api-prod.docuaction.io,app.docuaction.io,localhost,127.0.0.1'

@description('Comma-separated ALLOWED_ORIGINS (CORS).')
param allowedOrigins string = 'https://app.docuaction.io'

@description('Entra ID application (client) ID for SSO.')
param azureAdClientId string = ''

@description('Entra ID tenant ID for SSO.')
param azureAdTenantId string = ''

@description('Grant the site managed identity Key Vault Secrets User on the vault.')
param assignKeyVaultAccess bool = true

@description('Tags applied to the plan and site.')
param tags object = {}

// App Service Plan (Linux => reserved: true).
resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: appServicePlanName
  location: location
  tags: tags
  kind: 'linux'
  sku: {
    name: skuName
    tier: skuTier
    capacity: skuCapacity
  }
  properties: {
    reserved: true
  }
}

// The Web App. Key Vault references resolve via the system-assigned identity.
resource webApp 'Microsoft.Web/sites@2024-04-01' = {
  name: webAppName
  location: location
  tags: tags
  kind: 'app,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    keyVaultReferenceIdentity: 'SystemAssigned'
    siteConfig: {
      linuxFxVersion: linuxFxVersion
      alwaysOn: true
      ftpsState: 'FtpsOnly'
      minTlsVersion: '1.2'
      http20Enabled: false
      healthCheckPath: healthCheckPath
      appCommandLine: appCommandLine
      appSettings: [
        { name: 'APP_URL', value: appUrl }
        { name: 'EMAIL_FROM', value: 'admin@docuaction.io' }
        { name: 'EMAIL_FROM_NAME', value: 'DocuAction Security' }
        { name: 'ALLOWED_ORIGINS', value: allowedOrigins }
        { name: 'ALLOWED_HOSTS', value: allowedHosts }
        { name: 'AZURE_AD_CLIENT_ID', value: azureAdClientId }
        { name: 'AZURE_AD_TENANT_ID', value: azureAdTenantId }
        { name: 'ENABLE_SCHEDULER', value: 'true' }
        { name: 'BULLETIN_AUTH_ENABLED', value: 'true' }
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'false' }
        { name: 'ENABLE_ORYX_BUILD', value: 'false' }
        { name: 'WEBSITES_CONTAINER_START_TIME_LIMIT', value: '300' }
        { name: 'WEBSITE_HTTPLOGGING_RETENTION_DAYS', value: '3' }
        { name: 'PYTHONPATH', value: '/home/site/wwwroot/pydeps' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
        { name: 'APPINSIGHTS_INSTRUMENTATIONKEY', value: appInsightsInstrumentationKey }
        // ---- Key Vault reference placeholders (resolved by managed identity) ----
        { name: 'SECRET_KEY', value: '@Microsoft.KeyVault(VaultName=${keyVaultName};SecretName=SECRET-KEY)' }
        { name: 'ANTHROPIC_API_KEY', value: '@Microsoft.KeyVault(VaultName=${keyVaultName};SecretName=ANTHROPIC-API-KEY)' }
        { name: 'AZURE_AD_CLIENT_SECRET', value: '@Microsoft.KeyVault(VaultName=${keyVaultName};SecretName=AZURE-AD-CLIENT-SECRET)' }
        { name: 'SENDGRID_API_KEY', value: '@Microsoft.KeyVault(VaultName=${keyVaultName};SecretName=SENDGRID-API-KEY)' }
        // NOTE: DATABASE_URL is intentionally omitted here. Store the full
        // connection string as a Key Vault secret and reference it the same way,
        // rather than embedding the DB password in template output.
      ]
    }
  }
}

// Existing reference to the vault so we can attach a role assignment at its scope.
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

// Key Vault Secrets User (4633458b-17de-408a-b874-0445c86b69e6).
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource kvRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (assignKeyVaultAccess) {
  name: guid(keyVault.id, webApp.id, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: webApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output webAppId string = webApp.id
output webAppName string = webApp.name
output appServicePlanId string = plan.id
output defaultHostName string = webApp.properties.defaultHostName
output principalId string = webApp.identity.principalId
