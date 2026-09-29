# 把已签名的 APK 发布到 https://subtitle.gcsfg.work/releases/
# 每次发布把 VersionCode 加 1。必须用同一把签名，否则手机无法覆盖安装。
param(
    [Parameter(Mandatory = $true)]
    [string]$Apk,
    [Parameter(Mandatory = $true)]
    [int]$VersionCode,
    [string]$VersionName = "",
    [string]$Notes = ""
)

$ErrorActionPreference = "Stop"
if ($VersionCode -lt 1) {
    throw "VersionCode 必须从 1 开始，并且每次发布都要比上一版大。"
}
$apkPath = Resolve-Path -LiteralPath $Apk
if ($VersionName -eq "") {
    $VersionName = "$VersionCode"
}

$hash = (Get-FileHash -LiteralPath $apkPath -Algorithm SHA256).Hash.ToLowerInvariant()
$payload = [ordered]@{
    versionCode = $VersionCode
    versionName = $VersionName
    url          = "https://subtitle.gcsfg.work/releases/app.apk"
    sha256       = $hash
    notes        = $Notes
}
$jsonPath = Join-Path ([System.IO.Path]::GetTempPath()) "subtitle-latest.json"
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($jsonPath, (($payload | ConvertTo-Json -Compress) + "`n"), $utf8)

scp -o BatchMode=yes $jsonPath ubuntu@118.25.45.22:/tmp/subtitle-latest.json
if ($LASTEXITCODE -ne 0) { throw "上传版本说明失败" }
scp -o BatchMode=yes $apkPath ubuntu@118.25.45.22:/tmp/subtitle-app.apk
if ($LASTEXITCODE -ne 0) { throw "上传 APK 失败" }

ssh -o BatchMode=yes ubuntu@118.25.45.22 "sudo mkdir -p /var/www/subtitle-releases && sudo mv /tmp/subtitle-latest.json /var/www/subtitle-releases/latest.json && sudo mv /tmp/subtitle-app.apk /var/www/subtitle-releases/app.apk && sudo chmod 644 /var/www/subtitle-releases/latest.json /var/www/subtitle-releases/app.apk"
if ($LASTEXITCODE -ne 0) { throw "服务器保存更新文件失败" }

Write-Output "已发布 versionCode=$VersionCode sha256=$hash"
Write-Output "https://subtitle.gcsfg.work/releases/latest.json"
Write-Output "https://subtitle.gcsfg.work/releases/app.apk"
