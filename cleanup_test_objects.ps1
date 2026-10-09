# Dynamic cleanup script to purge users, computers, and all dynamically created OUs in nso.defmind
$baseDN = "DC=nso,DC=defmind"

# 1. Purge all test users
Write-Host "Purging all test users (test-usr-*)..." -ForegroundColor Cyan
Get-ADUser -Filter "Name -like 'test-usr-*'" -SearchBase $baseDN | Remove-ADUser -Confirm:$false
Write-Host "Users purged successfully." -ForegroundColor Green

# 2. Purge all test computers
Write-Host "Purging all test computers (test-svr-*)..." -ForegroundColor Cyan
Get-ADComputer -Filter "Name -like 'test-svr-*'" -SearchBase $baseDN | Remove-ADComputer -Confirm:$false
Write-Host "Computers purged successfully." -ForegroundColor Green

# 3. Purge all test groups
Write-Host "Purging all test groups (grp-test-*)..." -ForegroundColor Cyan
Get-ADGroup -Filter "Name -like 'grp-test-*'" -SearchBase $baseDN | Remove-ADGroup -Confirm:$false
Write-Host "Groups purged successfully." -ForegroundColor Green

# 4. Purge SPN service accounts
Write-Host "Purging SPN test users (svc-spn-*)..." -ForegroundColor Cyan
Get-ADUser -Filter "Name -like 'svc-spn-*'" -SearchBase $baseDN | Remove-ADUser -Confirm:$false
Write-Host "SPN users purged successfully." -ForegroundColor Green

# 5. Purge gap-fill objects
Write-Host "Purging gap-fill users (gapfill-usr-*)..." -ForegroundColor Cyan
Get-ADUser -Filter "Name -like 'gapfill-usr-*'" -SearchBase $baseDN | Remove-ADUser -Confirm:$false
Write-Host "Purging gap-fill computers (gapfill-svr-*)..." -ForegroundColor Cyan
Get-ADComputer -Filter "Name -like 'gapfill-svr-*'" -SearchBase $baseDN | Remove-ADComputer -Confirm:$false
Write-Host "Gap-fill objects purged successfully." -ForegroundColor Green

# 6. Unprotect and wipe all dynamically generated Server OUs
Write-Host "Unlocking and purging all generated Server OUs (ou-svr-*)..." -ForegroundColor Cyan
Get-ADOrganizationalUnit -Filter "Name -like 'ou-svr-*'" -SearchBase $baseDN | 
    Sort-Object {$_.DistinguishedName.Length} -Descending | ForEach-Object {
        Set-ADOrganizationalUnit $_ -ProtectedFromAccidentalDeletion $false
        Remove-ADOrganizationalUnit $_ -Confirm:$false
    }
Write-Host "Server OUs removed cleanly." -ForegroundColor Green

# 7. Unprotect and wipe all dynamically generated User OUs
Write-Host "Unlocking and purging all generated User OUs (ou-usr-*)..." -ForegroundColor Cyan
Get-ADOrganizationalUnit -Filter "Name -like 'ou-usr-*'" -SearchBase $baseDN | 
    Sort-Object {$_.DistinguishedName.Length} -Descending | ForEach-Object {
        Set-ADOrganizationalUnit $_ -ProtectedFromAccidentalDeletion $false
        Remove-ADOrganizationalUnit $_ -Confirm:$false
    }
Write-Host "User OUs removed cleanly." -ForegroundColor Green

Write-Host "The directory environment has been completely reset!" -ForegroundColor Green
