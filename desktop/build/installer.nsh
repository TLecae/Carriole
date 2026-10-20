; Compléments de l'installeur NSIS (installation pour tous les utilisateurs : l'installeur demande les droits
; d'administrateur, une seule fois, par la fenêtre habituelle de Windows).
; - langue choisie dans l'installeur, reprise par chaque utilisateur à son premier lancement (HKLM) ;
; - « etl » dans le PATH de la machine (facultatif pour l'utilisateur : l'application n'en a pas besoin) ;
; - arrêt du moteur avant une mise à jour ou une désinstallation (ses fichiers seraient verrouillés) ;
; - fermeture de la fenêtre : Carriole s'arrête complètement (par défaut) ou reste en arrière-plan (case de la page
;   « Options », valeur HKLM KeepRunning, reprise par l'application tant que l'utilisateur ne l'a pas changée).

!ifndef BUILD_UNINSTALLER
  !include nsDialogs.nsh
  !include LogicLib.nsh
  Var etlKeepRunning
  Var etlKeepRunningBox

  ; Les fonctions de la page sont dans la macro : elle est insérée après MUI2 (MUI_HEADER_TEXT), ce fichier avant.
  !macro customPageAfterChangeDir
    Page custom etlOptionsPage etlOptionsLeave

    Function etlOptionsPage
      ${If} $LANGUAGE == 1033
        !insertmacro MUI_HEADER_TEXT "Options" "Behaviour when the window is closed."
      ${Else}
        !insertmacro MUI_HEADER_TEXT "Options" "Comportement à la fermeture de la fenêtre."
      ${EndIf}
      nsDialogs::Create 1018
      Pop $0
      ${If} $LANGUAGE == 1033
        ${NSD_CreateLabel} 0 0 100% 40u "By default, closing the window shuts Carriole down completely. Keeping it in the background (icon next to the clock) lets runs finish and schedules start. This can be changed later in Settings."
        Pop $0
        ${NSD_CreateCheckbox} 0 48u 100% 12u "Keep Carriole running in the background when the window is closed"
      ${Else}
        ${NSD_CreateLabel} 0 0 100% 40u "Par défaut, fermer la fenêtre arrête complètement Carriole. La garder en arrière-plan (icône près de l'horloge) laisse les runs se terminer et les planifications se lancer. Modifiable ensuite dans les Paramètres."
        Pop $0
        ${NSD_CreateCheckbox} 0 48u 100% 12u "Laisser Carriole tourner en arrière-plan à la fermeture de la fenêtre"
      ${EndIf}
      Pop $etlKeepRunningBox
      ${If} $etlKeepRunning == "1"
        ${NSD_Check} $etlKeepRunningBox
      ${EndIf}
      nsDialogs::Show
    FunctionEnd

    Function etlOptionsLeave
      ${NSD_GetState} $etlKeepRunningBox $0
      ${If} $0 == ${BST_CHECKED}
        StrCpy $etlKeepRunning "1"
      ${Else}
        StrCpy $etlKeepRunning "0"
      ${EndIf}
    FunctionEnd
  !macroend
!endif

!macro customInit
  ; Mise à jour : la case « arrière-plan » reprend le choix précédent.
  SetRegView 64
  ReadRegStr $etlKeepRunning HKLM "Software\Carriole" "KeepRunning"
  ; Mise à jour : arrêter le moteur de l'installation précédente (par machine, ou par utilisateur d'une ancienne version ;
  ; « Atelier ETL » : ancien nom de l'application).
  IfFileExists "$PROGRAMFILES64\Carriole\resources\bin\stop.ps1" 0 +2
    nsExec::Exec 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PROGRAMFILES64\Carriole\resources\bin\stop.ps1" -Resources "$PROGRAMFILES64\Carriole\resources"'
  IfFileExists "$PROGRAMFILES64\Atelier ETL\resources\bin\stop.ps1" 0 +2
    nsExec::Exec 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PROGRAMFILES64\Atelier ETL\resources\bin\stop.ps1" -Resources "$PROGRAMFILES64\Atelier ETL\resources"'
  IfFileExists "$LOCALAPPDATA\Programs\Atelier ETL\resources\bin\stop.ps1" 0 +2
    nsExec::Exec 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$LOCALAPPDATA\Programs\Atelier ETL\resources\bin\stop.ps1" -Resources "$LOCALAPPDATA\Programs\Atelier ETL\resources"'
!macroend

!macro customInstall
  ; Langue choisie dans l'installeur (1033 = anglais, sinon français) : l'application la reprend à son premier lancement.
  SetRegView 64
  StrCmp $LANGUAGE 1033 0 etlLangFr
    WriteRegStr HKLM "Software\Carriole" "Language" "en"
    Goto etlLangDone
  etlLangFr:
    WriteRegStr HKLM "Software\Carriole" "Language" "fr"
  etlLangDone:
  StrCmp $etlKeepRunning "1" +2 0
    StrCpy $etlKeepRunning "0"
  WriteRegStr HKLM "Software\Carriole" "KeepRunning" $etlKeepRunning
  nsExec::Exec 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\resources\bin\path.ps1" -Action add -Dir "$INSTDIR\resources\bin"'
  Pop $0
!macroend

!macro customUnInit
  nsExec::Exec 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\resources\bin\stop.ps1" -Resources "$INSTDIR\resources"'
!macroend

!macro customUnInstall
  nsExec::Exec 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\resources\bin\path.ps1" -Action remove -Dir "$INSTDIR\resources\bin"'
  Pop $0
  SetRegView 64
  DeleteRegKey HKLM "Software\Carriole"
  DeleteRegKey HKLM "Software\AtelierETL"  ; ancien nom
!macroend
