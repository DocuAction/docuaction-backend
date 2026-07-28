// ===========================================================================
// DocuAction — resource-group-scoped orchestrator
//
// Recreates the rg-docuaction-prod footprint from the live environment:
//   - Monitoring   : Log Analytics + workspace-based App Insights + action
//                    group + 4 metric alerts
//   - Key Vault    : RBAC, soft-delete + purge-protection, 90-day
//   - PostgreSQL   : Flexible Server v16, Burstable B1ms, 32 GB, public + FW
//   - App Service  : P0v3 Linux plan + Python 3.12 Web App (MI, /health, KV refs)
//   - Networking   : VNet + KV private endpoint + private DNS (additive)
//   - Static Web App: docuaction-frontend (Free)
//
// Microsoft Defender plans are subscription-scoped and live in
// modules/defender.bicep — deploy separately with `az deployment sub create`.
// ===========================================================================

targetScope = 'resourceGroup'

// -------------------------- Common ------------------------------------------
@description('Azure region for regional resources.')
param location string = resourceGroup().location

@description('Tags applied to every resource.')
param tags object = {}

@description('Entra ID tenant GUID.')
param tenantId string = subscription().tenantId

@description('Email address for monitoring alerts.')
param alertEmail string

// -------------------------- App Service -------------------------------------
param appServiceName string
param appServicePlanName string
param appServicePlanSkuName string = 'P0v3'
param appServicePlanSkuTier string = 'Premium0V3'
param linuxFxVersion string = 'PYTHON|3.12'
param healthCheckPath string = '/health'
param appUrl string = 'https://app.docuaction.io'
param allowedHosts string = '*.azurewebsites.net,api.docuaction.io,api-prod.docuaction.io,app.docuaction.io,localhost,127.0.0.1'
param allowedOrigins string = 'https://app.docuaction.io'
param azureAdClientId string = ''
param azureAdTenantId string = ''

// -------------------------- PostgreSQL --------------------------------------
param postgresServerName string
param postgresVersion string = '16'
param postgresSkuName string = 'Standard_B1ms'
param postgresSkuTier string = 'Burstable'
param postgresStorageSizeGB int = 32
param postgresBackupRetentionDays int = 7
param postgresAvailabilityZone string = '1'
param postgresAdminLogin string
@secure()
param postgresAdminPassword string

// -------------------------- Key Vault ---------------------------------------
param keyVaultName string
param keyVaultPublicNetworkAccess string = 'Enabled'
param createKeyVaultSecrets bool = true
@secure()
param secretKey string = ''
@secure()
param anthropicApiKey string = ''
@secure()
param azureAdClientSecret string = ''
@secure()
param sendGridApiKey string = ''

// -------------------------- Monitoring --------------------------------------
param logAnalyticsName string
param appInsightsName string
param actionGroupName string = 'docuaction-alerts'
param monitoringRetentionDays int = 90

// -------------------------- Networking --------------------------------------
param deployNetworking bool = true
param vnetName string = 'docuaction-vnet'
param vnetAddressPrefix string = '10.0.0.0/16'
param privateEndpointSubnetPrefix string = '10.0.1.0/24'

// -------------------------- Static Web App ----------------------------------
param staticWebAppName string
param staticWebAppLocation string = 'eastus2'
param staticWebAppSku string = 'Free'

// Deterministic target IDs for the metric alerts. Using resourceId() (not module
// outputs) keeps the monitoring module free of a circular dependency on the App
// Service, which itself consumes the App Insights connection string.
var webAppResourceId = resourceId('Microsoft.Web/sites', appServiceName)
var appServicePlanResourceId = resourceId('Microsoft.Web/serverfarms', appServicePlanName)
var postgresResourceId = resourceId('Microsoft.DBforPostgreSQL/flexibleServers', postgresServerName)

// ---------------------------------------------------------------------------
// Modules
// ---------------------------------------------------------------------------

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    location: location
    logAnalyticsName: logAnalyticsName
    appInsightsName: appInsightsName
    actionGroupName: actionGroupName
    alertEmail: alertEmail
    retentionInDays: monitoringRetentionDays
    webAppResourceId: webAppResourceId
    appServicePlanResourceId: appServicePlanResourceId
    postgresResourceId: postgresResourceId
    tags: tags
  }
}

module keyVault 'modules/keyVault.bicep' = {
  name: 'keyVault'
  params: {
    keyVaultName: keyVaultName
    location: location
    tenantId: tenantId
    softDeleteRetentionInDays: 90
    publicNetworkAccess: keyVaultPublicNetworkAccess
    createSecrets: createKeyVaultSecrets
    secretKey: secretKey
    anthropicApiKey: anthropicApiKey
    azureAdClientSecret: azureAdClientSecret
    sendGridApiKey: sendGridApiKey
    tags: tags
  }
}

module postgresql 'modules/postgresql.bicep' = {
  name: 'postgresql'
  params: {
    serverName: postgresServerName
    location: location
    postgresVersion: postgresVersion
    skuName: postgresSkuName
    skuTier: postgresSkuTier
    storageSizeGB: postgresStorageSizeGB
    backupRetentionDays: postgresBackupRetentionDays
    availabilityZone: postgresAvailabilityZone
    administratorLogin: postgresAdminLogin
    administratorLoginPassword: postgresAdminPassword
    tags: tags
  }
}

module appService 'modules/appService.bicep' = {
  name: 'appService'
  params: {
    appServicePlanName: appServicePlanName
    webAppName: appServiceName
    location: location
    skuName: appServicePlanSkuName
    skuTier: appServicePlanSkuTier
    linuxFxVersion: linuxFxVersion
    healthCheckPath: healthCheckPath
    keyVaultName: keyVaultName
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    appInsightsInstrumentationKey: monitoring.outputs.appInsightsInstrumentationKey
    appUrl: appUrl
    allowedHosts: allowedHosts
    allowedOrigins: allowedOrigins
    azureAdClientId: azureAdClientId
    azureAdTenantId: azureAdTenantId
    tags: tags
  }
  dependsOn: [
    keyVault
  ]
}

module networking 'modules/networking.bicep' = if (deployNetworking) {
  name: 'networking'
  params: {
    vnetName: vnetName
    location: location
    vnetAddressPrefix: vnetAddressPrefix
    privateEndpointSubnetPrefix: privateEndpointSubnetPrefix
    keyVaultId: keyVault.outputs.keyVaultId
    tags: tags
  }
}

module staticWebApp 'modules/staticWebApp.bicep' = {
  name: 'staticWebApp'
  params: {
    staticWebAppName: staticWebAppName
    location: staticWebAppLocation
    skuName: staticWebAppSku
    tags: tags
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------
output webAppName string = appService.outputs.webAppName
output webAppDefaultHostName string = appService.outputs.defaultHostName
output webAppPrincipalId string = appService.outputs.principalId
output postgresFqdn string = postgresql.outputs.fullyQualifiedDomainName
output keyVaultUri string = keyVault.outputs.keyVaultUri
output appInsightsConnectionString string = monitoring.outputs.appInsightsConnectionString
output staticWebAppDefaultHostname string = staticWebApp.outputs.defaultHostname
