// ---------------------------------------------------------------------------
// Monitoring module
// Recreates docuaction-logs (Log Analytics, PerGB2018, 90-day retention),
// docuaction-appinsights (workspace-based App Insights, 90-day), the
// docuaction-alerts action group (email imran@agtbi.com) and the four metric
// alerts (availability, 5xx, high CPU, DB availability).
//
// Alert target resource IDs are passed in as strings so this module can be
// deployed BEFORE the App Service / Postgres exist without a circular
// dependency on their outputs. Leave them empty to skip alert creation.
// ---------------------------------------------------------------------------

@description('Azure region for workspace + component (alerts are always global).')
param location string

@description('Log Analytics workspace name.')
param logAnalyticsName string

@description('Application Insights component name.')
param appInsightsName string

@description('Action group name.')
param actionGroupName string = 'docuaction-alerts'

@description('Action group short name (<=12 chars).')
param actionGroupShortName string = 'docualert'

@description('Alert notification email address.')
param alertEmail string

@description('Data retention (days) for workspace and component.')
@minValue(30)
@maxValue(730)
param retentionInDays int = 90

@description('App Service (Microsoft.Web/sites) resource ID for availability + 5xx alerts.')
param webAppResourceId string = ''

@description('App Service Plan (Microsoft.Web/serverfarms) resource ID for CPU alert.')
param appServicePlanResourceId string = ''

@description('PostgreSQL flexible server resource ID for DB availability alert.')
param postgresResourceId string = ''

@description('Tags applied to workspace + component.')
param tags object = {}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionInDays
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    IngestionMode: 'LogAnalytics'
    RetentionInDays: retentionInDays
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: actionGroupName
  location: 'Global'
  properties: {
    groupShortName: actionGroupShortName
    enabled: true
    emailReceivers: [
      {
        name: 'adminEmail'
        emailAddress: alertEmail
        useCommonAlertSchema: false
      }
    ]
  }
}

// ---- Metric alerts (created only when their target resource ID is supplied) ----

resource availabilityAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = if (!empty(webAppResourceId)) {
  name: 'docuaction-availability'
  location: 'global'
  properties: {
    description: 'DocuAction app health check failing'
    severity: 1
    enabled: true
    scopes: [ webAppResourceId ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          criterionType: 'StaticThresholdCriterion'
          name: 'cond0'
          metricNamespace: 'Microsoft.Web/sites'
          metricName: 'HealthCheckStatus'
          operator: 'LessThan'
          threshold: 100
          timeAggregation: 'Average'
        }
      ]
    }
    actions: [
      { actionGroupId: actionGroup.id }
    ]
  }
}

resource http5xxAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = if (!empty(webAppResourceId)) {
  name: 'docuaction-5xx-errors'
  location: 'global'
  properties: {
    description: 'DocuAction 5xx errors exceeding threshold'
    severity: 2
    enabled: true
    scopes: [ webAppResourceId ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          criterionType: 'StaticThresholdCriterion'
          name: 'cond0'
          metricNamespace: 'Microsoft.Web/sites'
          metricName: 'Http5xx'
          operator: 'GreaterThan'
          threshold: 10
          timeAggregation: 'Total'
        }
      ]
    }
    actions: [
      { actionGroupId: actionGroup.id }
    ]
  }
}

resource cpuAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = if (!empty(appServicePlanResourceId)) {
  name: 'docuaction-high-cpu'
  location: 'global'
  properties: {
    description: 'DocuAction CPU exceeding 80%'
    severity: 2
    enabled: true
    scopes: [ appServicePlanResourceId ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          criterionType: 'StaticThresholdCriterion'
          name: 'cond0'
          metricNamespace: 'Microsoft.Web/serverfarms'
          metricName: 'CpuPercentage'
          operator: 'GreaterThan'
          threshold: 80
          timeAggregation: 'Average'
        }
      ]
    }
    actions: [
      { actionGroupId: actionGroup.id }
    ]
  }
}

resource dbAvailabilityAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = if (!empty(postgresResourceId)) {
  name: 'docuaction-db-availability'
  location: 'global'
  properties: {
    description: 'DocuAction PostgreSQL not alive'
    severity: 1
    enabled: true
    scopes: [ postgresResourceId ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          criterionType: 'StaticThresholdCriterion'
          name: 'cond0'
          metricNamespace: 'Microsoft.DBforPostgreSQL/flexibleServers'
          metricName: 'is_db_alive'
          operator: 'LessThan'
          threshold: 1
          timeAggregation: 'Average'
        }
      ]
    }
    actions: [
      { actionGroupId: actionGroup.id }
    ]
  }
}

output logAnalyticsId string = logAnalytics.id
output appInsightsId string = appInsights.id
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output appInsightsInstrumentationKey string = appInsights.properties.InstrumentationKey
output actionGroupId string = actionGroup.id
