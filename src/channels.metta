;configured at runtime:
(= (commchannel) (empty))

;Connect all the channels:
(= (initChannels)
   (progn (log INFO "channels" "Initializing channels")
          (configure commchannel dashboard)
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


; Operator jobs are consumed on this thread, never on HTTP worker threads.
(= (dashboard-sync-views)
   (if (== (commchannel) dashboard)
       (let* (($catalog-query (quote (getSkills)))
              ($catalog (eval $catalog-query))
              ($goal-query (quote (telos-list-goals)))
              ($relation-query (quote (telos-list-relations)))
              ($report-query (quote (telos-report)))
              ($goals (collapse (eval $goal-query)))
              ($relations (collapse (eval $relation-query)))
              ($report (collapse (eval $report-query))))
         (py-call (channels.dashboard_sync_views $catalog $goals $relations $report)))
       ()))

(= (dashboard-process-control)
   (if (== (commchannel) dashboard)
       (progn
         (dashboard-sync-views)
         (let $job (py-call (channels.dashboard_next_control))
           (if (== $job ())
               ()
               (let ($id $source) $job
                 (let $expression (sread $source)
                   (let $result (catch (collapse (eval $expression)))
                     (progn (dashboard-sync-views)
                       (py-call (channels.dashboard_finish_control $id $result))))))))
         (dashboard-sync-views))
       ()))

(= (dashboard-apply-settings $json)
   (map-atom (py-call (config.apply_operator_json $json)) $entry
      (let ($key $value) $entry
        (progn
          (collapse (match &self (= ($key) $old) (remove-atom &self (= ($key) $old))))
          (add-atom &self (= ($key) $value))))))
