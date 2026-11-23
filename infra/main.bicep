// Infraestructura del servicio de firma (INI Firma).
// Despliegue:  az deployment group create -g rg-ini-firma-dev -f infra/main.bicep -p infra/dev.bicepparam
// Antes:       az deployment group what-if ... (mismos parámetros) para ver qué va a cambiar.
//
// Compila sin errores ni avisos con Bicep 0.49 (az bicep build / lint, 2026-11-19). Lo que falta
// comprobar en vivo: cuotas, disponibilidad de Flex Consumption en la región y nombres libres.

targetScope = 'resourceGroup'

@allowed(['dev', 'prod'])
param entorno string
param ubicacion string = resourceGroup().location
param nombreBase string = 'ini-firma'
@description('Object ID del grupo o usuario de TI que administra las imágenes de firma (rol Storage Blob Data Contributor sobre el contenedor).')
param administradoresFirmasObjectId string = ''
@description('Tamaño máximo del PDF aceptado, en MB.')
param maxPdfMb int = 25
@description('Días de vigencia de la URL SAS del visor. Tope real: 7 (clave de delegación de usuario); el API recorta.')
@minValue(1)
@maxValue(7)
param visorDias int = 7
@description('Días tras la última modificación a partir de los cuales el ciclo de vida borra los blobs del contenedor del visor.')
param visorRetencionDias int = 30
param etiquetas object = {
  division: 'INI'
  sistema: 'INI Firma'
  entorno: entorno
}

var sufijo = '${nombreBase}-${entorno}'
var nombreStorage = toLower(replace('st${nombreBase}${entorno}', '-', ''))
var contenedorFirmas = 'firmas'
var contenedorPaquete = 'app-package'
var contenedorVisor = 'visor'

// ---------- Almacenamiento (requerido por Functions + imágenes de firma) ----------
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: nombreStorage
  location: ubicacion
  tags: etiquetas
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false // todo por identidad: ni claves ni SAS para las firmas
    publicNetworkAccess: 'Enabled'
    networkAcls: { defaultAction: 'Allow', bypass: 'AzureServices' }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: { enabled: true, days: 30 }
    containerDeleteRetentionPolicy: { enabled: true, days: 30 }
    // El control PDF de Power Apps carga la URL SAS del visor desde el navegador (apps.powerapps.com,
    // Teams, reproductor móvil): hace falta CORS de lectura. El SAS es la puerta; el origen abierto no
    // expone nada que no tenga firma válida. Si Seguridad lo pide, acotar a los orígenes verificados.
    cors: {
      corsRules: [
        {
          allowedOrigins: ['*']
          allowedMethods: ['GET', 'HEAD', 'OPTIONS']
          allowedHeaders: ['*']
          exposedHeaders: ['*']
          maxAgeInSeconds: 3600
        }
      ]
    }
  }
}

resource contFirmas 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: contenedorFirmas
  properties: { publicAccess: 'None' }
}

resource contPaquete 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: contenedorPaquete
  properties: { publicAccess: 'None' }
}

// Copias temporales de los originales para el visor de la app. F02 borra cada una al firmar; esta
// política borra lo que quede (rechazados, cancelados, errores) sin intervención.
resource contVisor 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: contenedorVisor
  properties: { publicAccess: 'None' }
}

resource cicloVida 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'borrar-visor'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: { blobTypes: ['blockBlob'], prefixMatch: ['${contenedorVisor}/'] }
            actions: { baseBlob: { delete: { daysAfterModificationGreaterThan: visorRetencionDias } } }
          }
        }
      ]
    }
  }
}

// ---------- Observabilidad ----------
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${sufijo}'
  location: ubicacion
  tags: etiquetas
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 90
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${sufijo}'
  location: ubicacion
  tags: etiquetas
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    DisableLocalAuth: false
  }
}

// ---------- Key Vault (secreto del cliente de Power Automate; certificado futuro para PAdES) ----------
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-${sufijo}'
  location: ubicacion
  tags: etiquetas
  properties: {
    tenantId: tenant().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
  }
}

// ---------- Function App (Flex Consumption, Python 3.12) ----------
resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: 'plan-${sufijo}'
  location: ubicacion
  tags: etiquetas
  kind: 'functionapp'
  sku: { tier: 'FlexConsumption', name: 'FC1' }
  properties: { reserved: true }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: 'func-${sufijo}'
  location: ubicacion
  tags: etiquetas
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storage.properties.primaryEndpoints.blob}${contenedorPaquete}'
          authentication: { type: 'SystemAssignedIdentity' }
        }
      }
      scaleAndConcurrency: {
        maximumInstanceCount: 40
        instanceMemoryMB: 2048
      }
      runtime: { name: 'python', version: '3.12' }
    }
    siteConfig: {
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appSettings: [
        { name: 'AzureWebJobsStorage__accountName', value: storage.name }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
        { name: 'FIRMAS_ORIGEN', value: 'blob' }
        { name: 'FIRMAS_STORAGE_URL', value: storage.properties.primaryEndpoints.blob }
        { name: 'FIRMAS_CONTENEDOR', value: contenedorFirmas }
        { name: 'FIRMAS_IMAGEN_POR_DEFECTO', value: 'gerente.png' }
        { name: 'FIRMA_MAX_PDF_MB', value: string(maxPdfMb) }
        { name: 'FIRMA_TEXTO_SELLO', value: 'Firmado electrónicamente por {nombre}{cargo} · {fecha} · {codigo} · Verif. {verif}' }
        { name: 'VISOR_ORIGEN', value: 'blob' }
        { name: 'VISOR_CONTENEDOR', value: contenedorVisor }
        { name: 'VISOR_DIAS', value: string(visorDias) }
      ]
    }
  }
}

// ---------- Roles: la Function lee las firmas y usa el storage por identidad ----------
// Para el visor no hace falta ningún rol nuevo: Storage Blob Data Owner sobre la cuenta ya permite escribir y
// borrar en `visor` y generar la clave de delegación de usuario con la que se firma el SAS.
var rolBlobDataOwner = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
var rolQueueDataContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '974c5e8b-45b9-4653-ba55-5f855dd0fb88')
var rolTableDataContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3')
var rolBlobDataContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
var rolKvSecretsUser = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')

resource raBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, functionApp.id, rolBlobDataOwner)
  scope: storage
  properties: { principalId: functionApp.identity.principalId, roleDefinitionId: rolBlobDataOwner, principalType: 'ServicePrincipal' }
}

resource raQueue 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, functionApp.id, rolQueueDataContributor)
  scope: storage
  properties: { principalId: functionApp.identity.principalId, roleDefinitionId: rolQueueDataContributor, principalType: 'ServicePrincipal' }
}

resource raTable 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, functionApp.id, rolTableDataContributor)
  scope: storage
  properties: { principalId: functionApp.identity.principalId, roleDefinitionId: rolTableDataContributor, principalType: 'ServicePrincipal' }
}

resource raKv 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, functionApp.id, rolKvSecretsUser)
  scope: keyVault
  properties: { principalId: functionApp.identity.principalId, roleDefinitionId: rolKvSecretsUser, principalType: 'ServicePrincipal' }
}

// TI sube y reemplaza las imágenes de firma SOLO en el contenedor `firmas`.
resource raAdminFirmas 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(administradoresFirmasObjectId)) {
  name: guid(contFirmas.id, administradoresFirmasObjectId, rolBlobDataContributor)
  scope: contFirmas
  properties: { principalId: administradoresFirmasObjectId, roleDefinitionId: rolBlobDataContributor }
}

output functionAppName string = functionApp.name
output functionAppUrl string = 'https://${functionApp.properties.defaultHostName}/api/v1'
output visorContainerUrl string = '${storage.properties.primaryEndpoints.blob}${contenedorVisor}'
output functionPrincipalId string = functionApp.identity.principalId
output storageAccountName string = storage.name
output firmasContainerUrl string = '${storage.properties.primaryEndpoints.blob}${contenedorFirmas}'
output keyVaultName string = keyVault.name
output appInsightsName string = appInsights.name
