# Sets only this PowerShell session's environment. Does not write files or send mail.
# Run once before starting the dashboard, and again in the worker terminal.
$opsfusionMailHost = Read-Host 'SMTP host [Enter for smtp.gmail.com]'
if ([string]::IsNullOrWhiteSpace($opsfusionMailHost)) { $opsfusionMailHost = 'smtp.gmail.com' }
$opsfusionMailMode = Read-Host 'TLS mode: starttls or ssl [Enter for starttls]'
if ([string]::IsNullOrWhiteSpace($opsfusionMailMode)) { $opsfusionMailMode = 'starttls' }
if ($opsfusionMailMode -notin @('starttls', 'ssl')) { throw 'Choose starttls or ssl. Unencrypted delivery is not supported.' }
$opsfusionMailDefaultPort = if ($opsfusionMailMode -eq 'ssl') { 465 } else { 587 }
$opsfusionMailPortText = Read-Host "SMTP port [Enter for $opsfusionMailDefaultPort]"
$opsfusionMailPort = $opsfusionMailDefaultPort
if (-not [string]::IsNullOrWhiteSpace($opsfusionMailPortText)) {
    if (-not [int]::TryParse($opsfusionMailPortText, [ref]$opsfusionMailPort)) { throw 'Use a numeric SMTP port.' }
}
if ($opsfusionMailPort -lt 1 -or $opsfusionMailPort -gt 65535) { throw 'SMTP port must be 1-65535.' }
$opsfusionMailUser = Read-Host 'SMTP username / sender email address'
$opsfusionMailFrom = Read-Host 'From email address [Enter to use username]'
if ([string]::IsNullOrWhiteSpace($opsfusionMailFrom)) { $opsfusionMailFrom = $opsfusionMailUser }
$opsfusionMailTo = Read-Host 'Your alert inbox email address (one address)'
$opsfusionMailSecret = Read-Host 'SMTP/app password (Gmail: use an App Password, not your usual password)' -AsSecureString
try {
    $opsfusionMailPassword = [System.Net.NetworkCredential]::new('', $opsfusionMailSecret).Password
    if ($opsfusionMailHost -eq 'smtp.gmail.com') { $opsfusionMailPassword = $opsfusionMailPassword.Replace(' ', '') }
    $env:OPSFUSION_SMTP_PASSWORD = $opsfusionMailPassword
    $env:OPSFUSION_EMAIL_SETTINGS = @{
        host = $opsfusionMailHost.Trim(); port = $opsfusionMailPort; security = $opsfusionMailMode
        username = $opsfusionMailUser.Trim(); 'from' = $opsfusionMailFrom.Trim(); to = $opsfusionMailTo.Trim()
        password_env = 'OPSFUSION_SMTP_PASSWORD'
    } | ConvertTo-Json -Compress
    Write-Host 'Email settings loaded in this terminal. No email was sent and no credentials were written to a file.'
} finally {
    Remove-Variable opsfusionMailPassword, opsfusionMailSecret -ErrorAction SilentlyContinue
}
