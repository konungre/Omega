;configured at runtime:
(= (commchannel) (empty))

;Connect all the channels:
(= (initChannels)
   (progn (log INFO "channels" "Initializing channels")
          (configure commchannel irc)
          (commChannelStart (commchannel))
          (if (== (commchannel) dashboard)
              (progn
                (add-skill dashboard-report-evidence "Report auditable evidence as a JSON object with title and content; returns its evidence ID." (report_json))
                (add-skill dashboard-report-improvement "Report a measured improvement result as JSON with title, summary, outcome (improved/unchanged/regressed/inconclusive), and evidence_ids referencing existing evidence. These are agent reports, not independent verification." (report_json)))
              ())))

(= (commChannelStart $commchannel)
   (py-call (channels.commChannelStart $commchannel)))

(= (commChannelReceive)
   (py-call (channels.commChannelReceive)))

(= (commChannelSend $message)
   (py-call (channels.commChannelSend $message)))

;Receive the latest user message considering all communication channels:
(= (receive)
   (commChannelReceive))

;Send a message to all communication channels:
!(change-state! &lastsend "")
(= (send $msg)
   (if (or (!= $msg (get-state &lastsend)) (== (commchannel) dashboard))
       (progn (change-state! &lastsend $msg)
              (let $safemsg (string-replace $msg  "\n" "\\n")
                   (let $temp (cut) (commChannelSend $safemsg)))) _))

(= (dashboard-report-evidence $report)
   (py-call (channels.dashboard_report "evidence" $report)))

(= (dashboard-report-improvement $report)
   (py-call (channels.dashboard_report "improvement" $report)))
