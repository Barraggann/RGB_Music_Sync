' Starts OpenRGB (SDK server, minimized) and the Apple Music RGB sync with no windows.
' install.ps1 puts a shortcut to this file in the Windows Startup folder (shell:startup).
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)

' OpenRGB 1.1+ installs a Windows service that is already running; a second copy
' would fight it for the same controllers and make the lights flicker.
Set running = GetObject("winmgmts:").ExecQuery("SELECT * FROM Win32_Process WHERE Name = 'OpenRGB.exe'")
openrgb = sh.ExpandEnvironmentStrings("%ProgramFiles%") & "\OpenRGB\OpenRGB.exe"
If running.Count = 0 And fso.FileExists(openrgb) Then
    sh.Run """" & openrgb & """ --server --startminimized", 0, False
    WScript.Sleep 10000
End If

' same for the sync itself: only one copy should drive the lights
Set syncs = GetObject("winmgmts:").ExecQuery("SELECT * FROM Win32_Process WHERE Name = 'pythonw.exe' AND CommandLine LIKE '%music_rgb.py%'")
If syncs.Count = 0 Then
    sh.Run "pyw """ & here & "\music_rgb.py""", 0, False
End If
