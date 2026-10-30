; Unit tests for the lenses and the skill entry points.
; Run from the root of an Omega checkout with this repository cloned into
; plugins/telos-goals -- see tests/run.sh.

!(import! &self ./tests/lib/utils)
!(import! &self ../src/helper.py)
!(import! &self ./src/utils)
!(import! &self ./src/skills)
!(import! &self ./src/loop)
!(import! &self ./plugins/telos-goals/telos_goals)

; Fixture: the atoms the skills below assert, written directly.
(goal alice-train     individual alice active)
(goal bob-share       individual bob   active)
(goal old-rust        individual alice abandoned)
(goal grant           individual alice achieved)
(goal dao-fair-access collective dao   active)
(goal gpu-quota       collective dao   proposed)
(rel conflicts  alice-train bob-share)
(rel supports   bob-share   dao-fair-access)
(rel depends-on dao-fair-access gpu-quota)
(rel subsumes   dao-fair-access gpu-quota)

; ---- Zero-arity lenses ----

!(test (expression-count-item (collapse (telos-conflicts)) (conflict-between alice-train bob-share)) 1)
!(test (expression-count-item (collapse (telos-conflicts)) (conflict-between bob-share alice-train)) 0)

!(test (expression-count-item (collapse (telos-collective-goals)) (collective-goal dao-fair-access dao)) 1)
!(test (expression-count-item (collapse (telos-collective-goals)) (collective-goal gpu-quota dao)) 1)
!(test (expression-count-item (collapse (telos-collective-goals)) (collective-goal alice-train alice)) 0)

!(test (expression-count-item (collapse (telos-achieved-goals)) (achieved-goal grant alice)) 1)
!(test (expression-count-item (collapse (telos-achieved-goals)) (achieved-goal alice-train alice)) 0)

!(test (expression-count-item (collapse (telos-abandoned-goals)) (abandoned-goal old-rust alice)) 1)
!(test (expression-count-item (collapse (telos-abandoned-goals)) (abandoned-goal grant alice)) 0)

; (rel subsumes <parent> <child>) reads parent then child.
!(test (expression-count-item (collapse (telos-subgoals)) (subgoal gpu-quota of dao-fair-access)) 1)
!(test (expression-count-item (collapse (telos-subgoals)) (subgoal dao-fair-access of gpu-quota)) 0)

; dao-fair-access depends on gpu-quota, which is only proposed, so it is blocked.
!(test (expression-count-item (collapse (telos-blocked)) (blocked dao-fair-access on gpu-quota)) 1)

!(test (expression-count-item (collapse (telos-aligned)) (aligns bob-share with dao-fair-access)) 1)
!(test (expression-count-item (collapse (telos-aligned)) (aligns alice-train with dao-fair-access)) 0)

; ---- Parameterised probes ----

!(test (expression-count-item (collapse (telos-goals-of alice)) (goal-of alice alice-train)) 1)
!(test (expression-count-item (collapse (telos-goals-of alice)) (goal-of alice old-rust)) 1)
!(test (expression-count-item (collapse (telos-goals-of alice)) (goal-of alice bob-share)) 0)
!(test (expression-count-item (collapse (telos-goals-of dao)) (goal-of dao dao-fair-access)) 0)

!(test (telos-achieved grant) True)
!(test (collapse (telos-achieved alice-train)) ())

; ---- The full reading includes every zero-arity lens ----

!(test (expression-count-item (collapse (telos-reading)) (conflict-between alice-train bob-share)) 1)
!(test (expression-count-item (collapse (telos-reading)) (collective-goal dao-fair-access dao)) 1)
!(test (expression-count-item (collapse (telos-reading)) (achieved-goal grant alice)) 1)
!(test (expression-count-item (collapse (telos-reading)) (abandoned-goal old-rust alice)) 1)
!(test (expression-count-item (collapse (telos-reading)) (subgoal gpu-quota of dao-fair-access)) 1)
!(test (expression-count-item (collapse (telos-reading)) (blocked dao-fair-access on gpu-quota)) 1)
!(test (expression-count-item (collapse (telos-reading)) (aligns bob-share with dao-fair-access)) 1)

; ---- Vocabulary check ----

!(test (telos-member individual (telos-scopes)) True)
!(test (telos-member group (telos-scopes)) False)
!(test (telos-member depends-on (telos-relations)) True)
!(test (telos-member causes (telos-relations)) False)

; ---- Skills: the agent passes everything after the skill name as one string ----

!(test (telos-goal-add "carol-ship individual carol active")
       (recorded (goal carol-ship individual carol active)))

; Recording the same goal twice must not duplicate it: every lens reports one
; result per matching atom.
!(test (telos-goal-add "carol-ship individual carol active")
       (already-recorded (goal carol-ship individual carol active)))
!(test (expression-count-item (collapse (telos-goals-of carol)) (goal-of carol carol-ship)) 1)

; One goal id carries one status: re-adding it with a different status is an
; error, not a second atom.
!(test (telos-goal-add "carol-ship individual carol achieved")
       (telos-error goal-exists (goal carol-ship individual carol active) use-telos-goal-status))

!(test (telos-goal-add "carol-rest individual carol bogus")
       (telos-error unknown-status bogus (active proposed achieved abandoned)))
!(test (telos-goal-add "carol-rest personal carol active")
       (telos-error unknown-scope personal (individual collective)))
!(test (telos-goal-add "carol-rest individual carol")
       (telos-error expected-arguments (goal_id scope owner status) (carol-rest individual carol)))

; A rejected goal is not in the graph.
!(test (collapse (telos-goals-of carol)) ((goal-of carol carol-ship)))

!(test (telos-rel-add "conflicts carol-ship alice-train")
       (recorded (rel conflicts carol-ship alice-train)))
!(test (telos-rel-add "causes carol-ship alice-train")
       (telos-error unknown-relation causes (supports conflicts subsumes depends-on)))
!(test (telos-rel-add "conflicts carol-ship")
       (telos-error expected-arguments (relation source_goal_id target_goal_id) (conflicts carol-ship)))

!(test (expression-count-item (telos-report) (conflict-between carol-ship alice-train)) 1)
!(test (expression-count-item (telos-report) (conflict-between carol-ship bob-share)) 0)

!(test (expression-count-item (telos-goals-owned-by "alice") (goal-of alice alice-train)) 1)
!(test (expression-count-item (telos-goals-owned-by "alice") (goal-of alice bob-share)) 0)

; ---- Status changes replace the goal atom rather than adding another ----

!(test (telos-goal-status "carol-ship achieved")
       (updated (goal carol-ship individual carol achieved) was active))
!(test (telos-goal-status "carol-ship achieved")
       (already-recorded (goal carol-ship individual carol achieved)))
!(test (telos-achieved carol-ship) True)
!(test (expression-count-item (collapse (telos-goals-of carol)) (goal-of carol carol-ship)) 1)
!(test (expression-count-item (collapse (telos-achieved-goals)) (achieved-goal carol-ship carol)) 1)

!(test (telos-goal-status "no-such-goal achieved") (telos-error unknown-goal no-such-goal))
!(test (telos-goal-status "carol-ship bogus")
       (telos-error unknown-status bogus (active proposed achieved abandoned)))
!(test (telos-goal-status "carol-ship")
       (telos-error expected-arguments (goal_id status) (carol-ship)))

; ---- The prompt extension names the skills, not raw atom assertion ----

!(test (contains-text (telos-prompt-extension) "telos-goal-add") True)
!(test (contains-text (telos-prompt-extension) "telos-report") True)
!(test (contains-text (telos-prompt-extension) "GOAL GRAPH") True)
