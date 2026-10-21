<#
Deploy Team Riders POS to Azure App Service (Linux, free F1 by default).

Usage:
    az login
    .\deploy-azure.ps1
    .\deploy-azure.ps1 -AppName teamriders-pos -ResourceGroup teamriders-rg -Location southafricanorth

Re-run the same command after changing the app to publish an update.
Data lives in /home/data/teamriders.db on the persistent App Service disk and is not touched by deployments.
#>
param(
    [string]$AppName = "teamriders-pos",
    [string]$ResourceGroup = "teamriders-rg",
    [string]$Location = "southafricanorth",
    [string]$Runtime = "PYTHON:3.13",
    [string]$Sku = "F1"
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

az account show --output none 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Not signed in. Run 'az login' first."
}

Write-Host "Ensuring resource group $ResourceGroup in $Location ..."
az group create --name $ResourceGroup --location $Location --output none

$planName = "$AppName-plan"
az appservice plan show --resource-group $ResourceGroup --name $planName --output none 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Creating App Service plan $planName ($Sku, Linux) ..."
    az appservice plan create --resource-group $ResourceGroup --name $planName --sku $Sku --is-linux --location $Location --output none
}

az webapp show --resource-group $ResourceGroup --name $AppName --output none 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Creating web app $AppName ($Runtime) ..."
    az webapp create --resource-group $ResourceGroup --plan $planName --name $AppName --runtime $Runtime --https-only true --output none
}

Write-Host "Applying startup command and settings ..."
az webapp config set --resource-group $ResourceGroup --name $AppName `
    --startup-file "bash /home/site/wwwroot/startup.sh" --ftps-state Disabled --output none

az webapp config appsettings set --resource-group $ResourceGroup --name $AppName --settings `
    SCM_DO_BUILD_DURING_DEPLOYMENT=true `
    WEBSITES_PORT=8000 `
    MX_DATA_DIR=/home/data `
    MX_DB_PATH=/home/data/teamriders.db `
    MX_SQLITE_JOURNAL=delete `
    PYTHONUNBUFFERED=1 --output none

$zip = Join-Path $env:TEMP "teamriders-deploy.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
$skip = @('data', '__pycache__', '.git', '.venv', 'deploy-azure.ps1')
$items = Get-ChildItem -Force | Where-Object { $skip -notcontains $_.Name }
Write-Host "Packaging $($items.Count) items ..."
Compress-Archive -Path ($items | ForEach-Object { $_.FullName }) -DestinationPath $zip -Force

Write-Host "Uploading and building (this can take a few minutes on the first run) ..."
az webapp deploy --resource-group $ResourceGroup --name $AppName --src-path $zip --type zip --output none

Write-Host ""
Write-Host "Deployed: https://$AppName.azurewebsites.net" -ForegroundColor Green
Write-Host "First visit shows the owner setup screen. Data is stored in /home/data/teamriders.db."
