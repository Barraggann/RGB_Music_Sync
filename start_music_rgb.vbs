' Starts OpenRGB (SDK server, minimized) and the Apple Music RGB sync with no windows.
' install.ps1 puts a shortcut to this file in the Windows Startup folder (shell:startup).
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)

openrgb = sh.ExpandEnvironmentStrings("%ProgramFiles%") & "\OpenRGB\OpenRGB.exe"
If fso.FileExists(openrgb) Then
    sh.Run """" & openrgb & """ --server --startminimized", 0, False
    WScript.Sleep 10000
End If

sh.Run "pyw """ & here & "\music_rgb.py""", 0, False
