param([string]$handle)
$raw = Invoke-WebRequest -Uri "http://localhost:8080/api/auth/login" -Method POST -ContentType "application/json" -Body '{"username": "vern", "password": "aegis2026vern"}' -UseBasicParsing
$token = ($raw.Content | ConvertFrom-Json).token
Invoke-WebRequest -Uri "http://localhost:8080/api/target" -Method POST -ContentType "application/json" -Headers @{"Authorization"="Bearer $token"} -Body "{`"target`": `"$handle`"}" -UseBasicParsing
Write-Host "PHANTOM LOCKED ON: @$handle"