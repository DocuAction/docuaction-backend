// ---------------------------------------------------------------------------
// PostgreSQL Flexible Server module
// Recreates docuaction-db: PostgreSQL 16, Burstable Standard_B1ms, 32 GB
// Premium_LRS storage with autogrow, public network access + firewall rule
// permitting Azure-internal services. Password authentication.
// ---------------------------------------------------------------------------

@description('Flexible Server name (globally unique).')
param serverName string

@description('Azure region for the server.')
param location string

@description('PostgreSQL major version.')
@allowed([
  '16'
  '15'
  '14'
])
param postgresVersion string = '16'

@description('Compute SKU name (e.g. Standard_B1ms).')
param skuName string = 'Standard_B1ms'

@description('Compute tier.')
@allowed([
  'Burstable'
  'GeneralPurpose'
  'MemoryOptimized'
])
param skuTier string = 'Burstable'

@description('Provisioned storage in GiB.')
param storageSizeGB int = 32

@description('Automatic storage growth.')
@allowed([
  'Enabled'
  'Disabled'
])
param storageAutoGrow string = 'Enabled'

@description('Backup retention in days.')
@minValue(7)
@maxValue(35)
param backupRetentionDays int = 7

@description('Geo-redundant backup.')
@allowed([
  'Enabled'
  'Disabled'
])
param geoRedundantBackup string = 'Disabled'

@description('Availability zone for the primary. Empty lets Azure choose.')
param availabilityZone string = '1'

@description('Administrator login name.')
param administratorLogin string

@description('Administrator password. Placeholder only in params files.')
@secure()
param administratorLoginPassword string

@description('Allow other Azure services (0.0.0.0 rule) to reach the server.')
param allowAzureServices bool = true

@description('Tags applied to the server.')
param tags object = {}

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: serverName
  location: location
  tags: tags
  sku: {
    name: skuName
    tier: skuTier
  }
  properties: {
    version: postgresVersion
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    availabilityZone: availabilityZone
    storage: {
      storageSizeGB: storageSizeGB
      autoGrow: storageAutoGrow
    }
    backup: {
      backupRetentionDays: backupRetentionDays
      geoRedundantBackup: geoRedundantBackup
    }
    highAvailability: {
      mode: 'Disabled'
    }
    network: {
      publicNetworkAccess: 'Enabled'
    }
    authConfig: {
      activeDirectoryAuth: 'Disabled'
      passwordAuth: 'Enabled'
    }
  }
}

// Firewall rule permitting Azure-internal traffic (0.0.0.0 sentinel).
resource allowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = if (allowAzureServices) {
  parent: postgres
  name: 'AllowAllAzureServicesAndResourcesWithinAzureIps'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

output postgresId string = postgres.id
output postgresName string = postgres.name
output fullyQualifiedDomainName string = postgres.properties.fullyQualifiedDomainName
