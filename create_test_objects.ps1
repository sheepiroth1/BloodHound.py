param (
    [Parameter(Mandatory = $false)]
    [string]$BaseDN = "DC=nso,DC=defmind",

    [Parameter(Mandatory = $true, HelpMessage = "Total number of computer accounts to generate")]
    [int]$TotalComputers,

    [Parameter(Mandatory = $true, HelpMessage = "Total number of user accounts to generate")]
    [int]$TotalUsers,

    [Parameter(Mandatory = $true, HelpMessage = "Total number of groups to generate")]
    [int]$TotalGroups = 50,

    [Parameter(Mandatory = $true, HelpMessage = "Number of top-level parent OUs for both tracks")]
    [int]$ParentOuCount,

    [Parameter(Mandatory = $true, HelpMessage = "Maximum nesting depth per parent line")]
    [int]$MaxDepth,

    [Parameter(Mandatory = $false, HelpMessage = "Number of users/computers placed outside custom OUs for gap-fill testing")]
    [int]$GapFillObjects = 10
)

$serverOUs = @()
$userOUs   = @()

# --- DYNAMIC SERVER OU GENERATION ---
Write-Host "Dynamically building nested Server OUs ($ParentOuCount parents up to depth $MaxDepth)..." -ForegroundColor Cyan
for ($p = 1; $p -le $ParentOuCount; $p++) {
    $parentName = "ou-svr-$p"
    $currentPathString = "OU=$parentName,$BaseDN"

    try {
        New-ADOrganizationalUnit -Name $parentName -Path "$BaseDN" -ErrorAction Stop
        $serverOUs += $currentPathString
    } catch {
        Write-Warning "Failed to create parent server OU $parentName : $_"
    }

    $runningPathPrefix = "OU=$parentName"

    for ($d = 1; $d -le ($MaxDepth - 1); $d++) {
        $childName = "$parentName-$d"

        try {
            New-ADOrganizationalUnit -Name $childName -Path "$runningPathPrefix,$BaseDN" -ErrorAction Stop
            $runningPathPrefix = "OU=$childName,$runningPathPrefix"
            $serverOUs += "$runningPathPrefix,$BaseDN"
        } catch {
            Write-Warning "Failed to create child server OU $childName : $_"
            break
        }
    }
}

# --- DYNAMIC USER OU GENERATION ---
Write-Host "Dynamically building nested User OUs ($ParentOuCount parents up to depth $MaxDepth)..." -ForegroundColor Cyan
for ($p = 1; $p -le $ParentOuCount; $p++) {
    $parentName = "ou-usr-$p"
    $currentPathString = "OU=$parentName,$BaseDN"

    try {
        New-ADOrganizationalUnit -Name $parentName -Path "$BaseDN" -ErrorAction Stop
        $userOUs += $currentPathString
    } catch {
        Write-Warning "Failed to create parent user OU $parentName : $_"
    }

    $runningPathPrefix = "OU=$parentName"

    for ($d = 1; $d -le ($MaxDepth - 1); $d++) {
        $childName = "$parentName-$d"

        try {
            New-ADOrganizationalUnit -Name $childName -Path "$runningPathPrefix,$BaseDN" -ErrorAction Stop
            $runningPathPrefix = "OU=$childName,$runningPathPrefix"
            $userOUs += "$runningPathPrefix,$BaseDN"
        } catch {
            Write-Warning "Failed to create child user OU $childName : $_"
            break
        }
    }
}

# --- DYNAMIC COMPUTER INJECTION ---
Write-Host "Injecting $TotalComputers computers randomly across the built server OUs..." -ForegroundColor Cyan
for ($i = 1; $i -le $TotalComputers; $i++) {
    $compName = "test-svr-$i"
    $randomPath = Get-Random -InputObject $serverOUs

    try {
        New-ADComputer -Name $compName -Path $randomPath -Enabled $true -ErrorAction Stop
    } catch {
        Write-Warning "Failed to create computer $compName : $_"
    }
}

# --- DYNAMIC USER INJECTION ---
Write-Host "Injecting $TotalUsers users randomly across the built user OUs..." -ForegroundColor Cyan
for ($j = 1; $j -le $TotalUsers; $j++) {
    $userName = "test-usr-$j"
    $randomPath = Get-Random -InputObject $userOUs

    try {
        $securePassword = ConvertTo-SecureString "P@ssword123!" -AsPlainText -Force
        New-ADUser -Name $userName -SamAccountName $userName -UserPrincipalName "$userName@nso.defmind" -Path $randomPath -AccountPassword $securePassword -Enabled $true -ErrorAction Stop
    } catch {
        Write-Warning "Failed to create user $userName : $_"
    }
}

# --- DYNAMIC GROUP INJECTION ---
Write-Host "Injecting $TotalGroups groups randomly across the built user OUs..." -ForegroundColor Cyan
$allOUs = $serverOUs + $userOUs
$createdGroups = [System.Collections.Generic.List[string]]::new()
for ($g = 1; $g -le $TotalGroups; $g++) {
    $groupName = "grp-test-$g"
    $randomPath = Get-Random -InputObject $allOUs

    try {
        New-ADGroup -Name $groupName -SamAccountName $groupName -GroupScope Global -Path $randomPath -ErrorAction Stop
        $createdGroups.Add($groupName)
    } catch {
        Write-Warning "Failed to create group $groupName : $_"
    }
}

# --- ADD RANDOM MEMBERS TO GROUPS ---
Write-Host "Adding random members to groups..." -ForegroundColor Cyan
$allUsers = 1..$TotalUsers | ForEach-Object { "test-usr-$_" }
foreach ($grp in $createdGroups) {
    $memberCount = Get-Random -Minimum 1 -Maximum ([Math]::Min(10, $TotalUsers + 1))
    $members = $allUsers | Get-Random -Count $memberCount
    try {
        Add-ADGroupMember -Identity $grp -Members $members -ErrorAction Stop
    } catch {
        Write-Warning "Failed to add members to $grp : $_"
    }
}

# --- NESTED GROUP MEMBERSHIP ---
Write-Host "Creating nested group memberships..." -ForegroundColor Cyan
if ($createdGroups.Count -ge 4) {
    try {
        Add-ADGroupMember -Identity $createdGroups[0] -Members $createdGroups[1] -ErrorAction Stop
        Add-ADGroupMember -Identity $createdGroups[1] -Members $createdGroups[2] -ErrorAction Stop
        Write-Host "  Nested: $($createdGroups[0]) -> $($createdGroups[1]) -> $($createdGroups[2])" -ForegroundColor DarkCyan
    } catch {
        Write-Warning "Failed to create nested group membership: $_"
    }
}

# --- SPN USERS (delegation testing) ---
Write-Host "Creating users with SPNs for delegation testing..." -ForegroundColor Cyan
$spnPath = Get-Random -InputObject $userOUs
for ($s = 1; $s -le 3; $s++) {
    $spnUser = "svc-spn-$s"
    try {
        $securePassword = ConvertTo-SecureString "P@ssword123!" -AsPlainText -Force
        New-ADUser -Name $spnUser -SamAccountName $spnUser -UserPrincipalName "$spnUser@nso.defmind" `
            -Path $spnPath -AccountPassword $securePassword -Enabled $true `
            -ServicePrincipalNames @("HTTP/$spnUser.nso.defmind", "MSSQLSvc/$spnUser.nso.defmind:1433") `
            -ErrorAction Stop
        Write-Host "  Created SPN user: $spnUser" -ForegroundColor DarkCyan
    } catch {
        Write-Warning "Failed to create SPN user $spnUser : $_"
    }
}

# --- GAP-FILL OBJECTS (outside custom OUs) ---
Write-Host "Creating $GapFillObjects objects in CN=Users and CN=Computers for gap-fill testing..." -ForegroundColor Cyan
$usersContainer = "CN=Users,$BaseDN"
$computersContainer = "CN=Computers,$BaseDN"

for ($gf = 1; $gf -le $GapFillObjects; $gf++) {
    if ($gf % 2 -eq 0) {
        $gfUser = "gapfill-usr-$gf"
        try {
            $securePassword = ConvertTo-SecureString "P@ssword123!" -AsPlainText -Force
            New-ADUser -Name $gfUser -SamAccountName $gfUser -UserPrincipalName "$gfUser@nso.defmind" `
                -Path $usersContainer -AccountPassword $securePassword -Enabled $true -ErrorAction Stop
        } catch {
            Write-Warning "Failed to create gap-fill user $gfUser : $_"
        }
    } else {
        $gfComp = "gapfill-svr-$gf"
        try {
            New-ADComputer -Name $gfComp -Path $computersContainer -Enabled $true -ErrorAction Stop
        } catch {
            Write-Warning "Failed to create gap-fill computer $gfComp : $_"
        }
    }
}

Write-Host ""
Write-Host "=== Summary ===" -ForegroundColor Green
Write-Host "  Server OUs : $($serverOUs.Count)" -ForegroundColor Green
Write-Host "  User OUs   : $($userOUs.Count)" -ForegroundColor Green
Write-Host "  Computers  : $TotalComputers (in OUs) + $([Math]::Floor($GapFillObjects / 2)) (gap-fill)" -ForegroundColor Green
Write-Host "  Users      : $TotalUsers (in OUs) + $([Math]::Ceiling($GapFillObjects / 2)) (gap-fill) + 3 (SPN)" -ForegroundColor Green
Write-Host "  Groups     : $TotalGroups (with random members + nested chains)" -ForegroundColor Green
Write-Host ""
Write-Host "Gap-fill objects are in CN=Users and CN=Computers (default containers)." -ForegroundColor Yellow
Write-Host "Per-location enumeration will find OU objects; gap-fill SUBTREE will catch the rest." -ForegroundColor Yellow
Write-Host ""
Write-Host "Dynamic generation completed successfully!" -ForegroundColor Green
