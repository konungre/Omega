; telos_goals -- Omega plugin: a goal graph the agent populates and queries.
;
; Entry point is (loadOmegaPlugin), called by the Omega MeTTa plugin loader,
; with (loadOmegaClawPlugin) alongside it for the OmegaClaw build.
; See README.md for installation and docs/reference.md for the full API.
;
; The plugin owns its own MeTTa space (&plugin-telos_goals), so every atom it
; asserts and every query it runs live in that one space. The skills below are
; the only supported way in: asserting goal atoms straight from the `metta`
; skill writes them into the agent's space instead, where these lenses do not
; look for them.

; Registration is unconditional: a plugin is turned on and off by its entry in
; config/plugins.yaml, and a configured flag would not be read here anyway --
; (configure ...) writes the value into the agent's space, while a call in this
; file resolves against the plugin's own space.
;
; Two entry points, one body. singnet/Omega calls (loadOmegaPlugin); the
; OmegaClaw build in singularitynet/omega:latest renamed it to
; (loadOmegaClawPlugin) and its loader matches on that name alone, so a plugin
; that defines only the documented name loads on one and silently stops the
; agent on the other.
(= (loadOmegaPlugin) (telos-load-plugin))
(= (loadOmegaClawPlugin) (telos-load-plugin))

(= (telos-load-plugin)
   (progn
     (log INFO "telos-goals-plugin" "Registering the goal graph skills")
     (add-skill
       telos-goal-add
       "Record a goal in the goal graph. scope is individual or collective, status is active, proposed, achieved or abandoned"
       (goal_id scope owner status))
     (add-skill
       telos-goal-status
       "Change the status of a goal already in the goal graph to active, proposed, achieved or abandoned"
       (goal_id status))
     (add-skill
       telos-rel-add
       "Record a relation between two goals in the goal graph. relation is supports, conflicts, subsumes or depends-on; subsumes reads parent then child, depends-on reads goal then prerequisite"
       (relation source_goal_id target_goal_id))
     (add-skill
       telos-report
       "Read the whole goal graph: conflicts, blocked goals, individual goals aligned with collective ones, sub-goals, collective goals, achieved and abandoned goals"
       ())
     (add-skill
       telos-goals-owned-by
       "List the individual goals of one stakeholder"
       (owner))
     (add-prompt-extension telos-goals (telos-prompt-extension))
     ; The loader folds this result looking for an (Error ...) tuple and calls
     ; car-atom on it, so the entry point returns () rather than the True that
     ; add-prompt-extension would otherwise hand back.
     ()))

; ---- Vocabulary ------------------------------------------------------------
;
; Schema, all relations read src -> dst:
;   (goal <id> <scope> <owner> <status>)
;   (rel supports   <src> <dst>)     achieving src advances dst
;   (rel conflicts  <src> <dst>)     src and dst cannot both be achieved
;   (rel subsumes   <parent> <child>) parent is the broader goal
;   (rel depends-on <src> <dst>)     src cannot progress until dst is achieved

(= (telos-scopes) (individual collective))
(= (telos-statuses) (active proposed achieved abandoned))
(= (telos-relations) (supports conflicts subsumes depends-on))

; Deterministic membership: one answer, so a bad token cannot also read as good.
(= (telos-member $x $list)
   (if (== () $list)
       False
       (if (== $x (car-atom $list))
           True
           (telos-member $x (cdr-atom $list)))))

; ---- Skills ----------------------------------------------------------------
;
; A skill receives everything after its name as a single string (see
; helper.balance_parentheses), so the arguments are read back into an
; expression here and checked before anything is asserted.

(= (telos-read-args $args)
   (sread (strings-concat ("(" $args ")"))))

(= (telos-goal-add $args)
   (case (telos-read-args $args)
     ((($id $scope $owner $status)
       (if (not (telos-member $scope (telos-scopes)))
           (telos-error unknown-scope $scope (telos-scopes))
           (if (not (telos-member $status (telos-statuses)))
               (telos-error unknown-status $status (telos-statuses))
               (telos-goal-assert $id $scope $owner $status))))
      ($else
       (telos-error expected-arguments (goal_id scope owner status) $else)))))

(= (telos-goal-status $args)
   (case (telos-read-args $args)
     ((($id $status)
       (if (not (telos-member $status (telos-statuses)))
           (telos-error unknown-status $status (telos-statuses))
           (telos-set-status $id $status)))
      ($else
       (telos-error expected-arguments (goal_id status) $else)))))

; One goal id carries one status. Re-adding an id that is already known is an
; error rather than a second atom, because two (goal <id> ...) atoms with
; different statuses would both answer the lenses below: a goal could read as
; achieved and still block its dependants.
(= (telos-goal-assert $id $scope $owner $status)
   (let $found (collapse (match &self (goal $id $s $o $st) (goal $id $s $o $st)))
     (if (== $found ())
         (progn
           (add-atom &self (goal $id $scope $owner $status))
           (recorded (goal $id $scope $owner $status)))
         (if (== (car-atom $found) (goal $id $scope $owner $status))
             (already-recorded (goal $id $scope $owner $status))
             (telos-error goal-exists (car-atom $found) use-telos-goal-status)))))

(= (telos-set-status $id $status)
   (let $found (collapse (match &self (goal $id $s $o $st) (goal $id $s $o $st)))
     (if (== $found ())
         (telos-error unknown-goal $id)
         (case (car-atom $found)
           (((goal $gid $scope $owner $old)
             (if (== $old $status)
                 (already-recorded (goal $id $scope $owner $status))
                 (progn
                   (let $_ (collapse (match &self (goal $id $s $o $st)
                                       (remove-atom &self (goal $id $s $o $st)))) ())
                   (add-atom &self (goal $id $scope $owner $status))
                   (updated (goal $id $scope $owner $status) was $old)))))))))

(= (telos-rel-add $args)
   (case (telos-read-args $args)
     ((($relation $src $dst)
       (if (not (telos-member $relation (telos-relations)))
           (telos-error unknown-relation $relation (telos-relations))
           (telos-assert (rel $relation $src $dst))))
      ($else
       (telos-error expected-arguments (relation source_goal_id target_goal_id) $else)))))

; Assert a relation once: re-recording it must not duplicate it, because every
; lens below reports one result per matching atom.
(= (telos-assert $atom)
   (if (== () (collapse (match &self $atom $atom)))
       (progn
         (add-atom &self $atom)
         (recorded $atom))
       (already-recorded $atom)))

(= (telos-report)
   (collapse (telos-reading)))

(= (telos-goals-owned-by $owner)
   (collapse (telos-goals-of (car-atom (telos-read-args $owner)))))

; ---- Lenses: zero-arity, every one folded into (telos-reading) --------------

; All conflict pairs the agent must surface (across ALL goals).
(= (telos-conflicts)
   (match &self (rel conflicts $a $b) (conflict-between $a $b)))

; Every collective goal (what the commons wants).
(= (telos-collective-goals)
   (match &self (goal $g collective $owner $status) (collective-goal $g $owner)))

; Every achieved goal, any scope, any owner.
(= (telos-achieved-goals)
   (match &self (goal $g $scope $owner achieved) (achieved-goal $g $owner)))

; Every abandoned goal. Surfacing these stops the agent chasing a goal its owner dropped.
(= (telos-abandoned-goals)
   (match &self (goal $g $scope $owner abandoned) (abandoned-goal $g $owner)))

; Sub-goal structure: (rel subsumes <parent> <child>) yields (subgoal <child> of <parent>).
(= (telos-subgoals)
   (match &self (rel subsumes $parent $child) (subgoal $child of $parent)))

; BLOCKED: a goal that depends-on another goal that is not yet achieved.
; An abandoned dependency still blocks: it will never become achieved.
(= (telos-blocked)
   (match &self (rel depends-on $g $dep)
      (match &self (goal $dep $s2 $o2 $st2)
         (if (== $st2 achieved) (empty) (blocked $g on $dep)))))

; Cross-level ALIGNMENT: an individual goal that SUPPORTS a collective goal.
(= (telos-aligned)
   (match &self (rel supports $i $c)
      (match &self (goal $i individual $o1 $s1)
         (match &self (goal $c collective $o2 $s2)
            (aligns $i with $c)))))

; The full goal reading: the superposition of every zero-arity lens above.
; The parameterised probes below take an owner or a goal id, so they are
; queried on their own rather than folded into the reading.
(= (telos-reading)
   (superpose ((telos-conflicts)
               (telos-collective-goals)
               (telos-achieved-goals)
               (telos-abandoned-goals)
               (telos-subgoals)
               (telos-blocked)
               (telos-aligned))))

; ---- Lenses: parameterised, not part of (telos-reading) ---------------------

; Goals owned by ANY given stakeholder (individual goal understanding).
(= (telos-goals-of $owner)
   (match &self (goal $g individual $owner $status) (goal-of $owner $g)))

; A goal is ACHIEVED when its status atom says so.
(= (telos-achieved $g)
   (match &self (goal $g $scope $owner achieved) True))

; ---- Prompt extension ------------------------------------------------------
;
; Teaches the LLM what the graph is for and which skills read and write it.

(= (telos-prompt-extension)
   "GOAL GRAPH: when a message reveals what someone wants, record it and reason over it symbolically instead of guessing. Record a goal with: telos-goal-add goal_id individual|collective owner active|proposed|achieved|abandoned. Change a goal you already recorded with: telos-goal-status goal_id active|proposed|achieved|abandoned. Record a relation with: telos-rel-add supports|conflicts|subsumes|depends-on source_goal_id target_goal_id, where subsumes reads parent then child and depends-on reads goal then prerequisite. Read the graph with: telos-report, or telos-goals-owned-by owner for one stakeholder. Use these skills rather than asserting goal atoms through metta: the graph lives in the plugin's own space. Tell the user about every conflict-between and blocked result before acting on a goal.")

; Omega's structured provider bridge passes one string per declared parameter.
; Preserve the upstream one-string interface while supporting those typed calls.
(= (telos-goal-add $id $scope $owner $status)
   (telos-goal-add (strings-concat ($id " " $scope " " $owner " " $status))))
(= (telos-goal-status $id $status)
   (telos-goal-status (strings-concat ($id " " $status))))
(= (telos-rel-add $relation $source $target)
   (telos-rel-add (strings-concat ($relation " " $source " " $target))))

; Public inspection surface. Consumers must not assume a particular PeTTa
; build's internal namespace layout for the plugin-owned graph.
(= (telos-list-goals)
   (collapse (match &self (goal $id $scope $owner $status) (goal $id $scope $owner $status))))
(= (telos-list-relations)
   (collapse (match &self (rel $kind $source $target) (rel $kind $source $target))))
