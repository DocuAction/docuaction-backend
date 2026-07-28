// ---------------------------------------------------------------------------
// Networking module (additive hardening — NOT currently deployed in prod)
// Creates docuaction-vnet (10.0.0.0/16) with a private-endpoints subnet
// (10.0.1.0/24), a Key Vault private endpoint, the privatelink.vaultcore.azure.net
// private DNS zone, its VNet link, and the private DNS zone group binding.
//
// NOTE: prod Key Vault currently runs with publicNetworkAccess=Enabled and no
// private endpoint. This module models the target private-networking posture.
// Applying it does not by itself disable public access on the vault.
// ---------------------------------------------------------------------------

@description('Virtual network name.')
param vnetName string = 'docuaction-vnet'

@description('Azure region.')
param location string

@description('VNet address space.')
param vnetAddressPrefix string = '10.0.0.0/16'

@description('Private-endpoints subnet name.')
param privateEndpointSubnetName string = 'private-endpoints'

@description('Private-endpoints subnet prefix.')
param privateEndpointSubnetPrefix string = '10.0.1.0/24'

@description('Resource ID of the Key Vault to place behind a private endpoint.')
param keyVaultId string

@description('Tags applied to created resources.')
param tags object = {}

var privateDnsZoneName = 'privatelink.vaultcore.azure.net'
var keyVaultPrivateEndpointName = 'pe-${vnetName}-kv'

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [ vnetAddressPrefix ]
    }
    subnets: [
      {
        name: privateEndpointSubnetName
        properties: {
          addressPrefix: privateEndpointSubnetPrefix
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

resource privateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: privateDnsZoneName
  location: 'global'
  tags: tags
}

resource dnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: privateDnsZone
  name: '${vnetName}-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

resource keyVaultPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: keyVaultPrivateEndpointName
  location: location
  tags: tags
  properties: {
    subnet: {
      id: '${vnet.id}/subnets/${privateEndpointSubnetName}'
    }
    privateLinkServiceConnections: [
      {
        name: 'kvConnection'
        properties: {
          privateLinkServiceId: keyVaultId
          groupIds: [ 'vault' ]
        }
      }
    ]
  }
}

resource keyVaultPrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: keyVaultPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'vaultcore'
        properties: {
          privateDnsZoneId: privateDnsZone.id
        }
      }
    ]
  }
}

output vnetId string = vnet.id
output privateEndpointSubnetId string = '${vnet.id}/subnets/${privateEndpointSubnetName}'
output privateDnsZoneId string = privateDnsZone.id
output keyVaultPrivateEndpointId string = keyVaultPrivateEndpoint.id
