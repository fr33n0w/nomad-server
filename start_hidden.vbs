' Starts nomad-server on Windows with no window at all.
' Open the web UI in the browser; stop it with "python nomad_server.py --stop"
' or the Stop server button in Settings. Full log in logs\server.log.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
sh.Run "pythonw """ & dir & "\nomad_server.py"" --service", 0, False
