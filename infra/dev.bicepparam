using './main.bicep'

param entorno = 'dev'
// Región del grupo rg-ini-firma-dev (eastus2 ofrece Flex Consumption)
// Object ID de quien administra las imágenes de firma (az ad signed-in-user show --query id -o tsv)
param administradoresFirmasObjectId = '9dfee0bb-8e78-44c9-8603-a642ba8800a1' // jlmunoz@pancanal.com
param maxPdfMb = 25
param visorDias = 7
param visorRetencionDias = 30
