using './main.bicep'

param entorno = 'dev'
// param ubicacion = 'eastus2'
// Object ID del grupo de TI que administra las imágenes (az ad group show -g "<grupo>" --query id -o tsv)
param administradoresFirmasObjectId = ''
param maxPdfMb = 25
