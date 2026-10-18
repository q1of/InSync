' InSync — запуск без окна-лаунчера.
' Двойной клик по этому файлу запускает run-local.bat СКРЫТО (своего окна нет);
' откроются только нужные окна: webapp, ngrok, backend.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
sh.Run "cmd /c """ & dir & "\run-local.bat""", 0, False
