; Integration test: the plugin is loaded the way the agent loads it, through
; the Omega MeTTa plugin loader, and is then used only through its skills.
; Run from the root of an Omega checkout with this repository cloned into
; plugins/telos-goals -- see tests/run.sh.
;
; The loader is called as (loadMettaPlugin <name> <location>) rather than
; (loadPlugin (metta <name> <location>)): `metta` is itself a skill function,
; so that second form compiles to a partial application, the call finds no
; matching clause, and the test passes on an empty result without ever loading
; anything.

!(import! &self ./tests/lib/utils)
!(import! &self ../../../src/helper.py)
!(import! &self ./src/utils)
!(import! &self ../../../src/config.py)
!(import! &self ./src/config)
!(import! &self ../../../src/logger.py)
!(import! &self ./src/log)
!(import! &self ../../../src/plugin.py)
!(import! &self ./src/plugin)
!(import! &self ./src/skills)
!(import! &self ./src/loop)

!(initConfig)
!(initLogger)

; The loader imports plugins/telos-goals/telos_goals.metta into its own space,
; calls the entry point there, and returns () when nothing went wrong.
!(test (loadMettaPlugin "telos_goals" "./plugins/telos-goals") ())

; The entry point registered the five skills and the prompt extension.
!(test (contains-text (swrite (getSkills)) "telos-goal-add") True)
!(test (contains-text (swrite (getSkills)) "telos-goal-status") True)
!(test (contains-text (swrite (getSkills)) "telos-rel-add") True)
!(test (contains-text (swrite (getSkills)) "telos-report") True)
!(test (contains-text (swrite (getSkills)) "telos-goals-owned-by") True)
!(test (contains-text (getPromptExtensions) "GOAL GRAPH") True)

; The graph is written and read through the skills, exactly as the agent loop
; evaluates them: one string argument each.
!(test (eval (telos-goal-add "alice-train individual alice active"))
       (recorded (goal alice-train individual alice active)))
!(test (eval (telos-goal-add "bob-share individual bob active"))
       (recorded (goal bob-share individual bob active)))
!(test (eval (telos-goal-add "dao-fair-access collective dao active"))
       (recorded (goal dao-fair-access collective dao active)))
!(test (eval (telos-goal-add "gpu-quota collective dao proposed"))
       (recorded (goal gpu-quota collective dao proposed)))
!(test (eval (telos-rel-add "conflicts alice-train bob-share"))
       (recorded (rel conflicts alice-train bob-share)))
!(test (eval (telos-rel-add "supports bob-share dao-fair-access"))
       (recorded (rel supports bob-share dao-fair-access)))
!(test (eval (telos-rel-add "depends-on dao-fair-access gpu-quota"))
       (recorded (rel depends-on dao-fair-access gpu-quota)))

!(test (expression-count-item (eval (telos-report)) (conflict-between alice-train bob-share)) 1)
!(test (expression-count-item (eval (telos-report)) (aligns bob-share with dao-fair-access)) 1)
!(test (expression-count-item (eval (telos-report)) (blocked dao-fair-access on gpu-quota)) 1)
!(test (expression-count-item (eval (telos-report)) (collective-goal dao-fair-access dao)) 1)

!(test (expression-count-item (eval (telos-goals-owned-by "alice")) (goal-of alice alice-train)) 1)
!(test (expression-count-item (eval (telos-goals-owned-by "alice")) (goal-of alice bob-share)) 0)

; Achieving the prerequisite clears the blocker, and only through the status
; skill: adding the goal again is refused.
!(test (eval (telos-goal-add "gpu-quota collective dao achieved"))
       (telos-error goal-exists (goal gpu-quota collective dao proposed) use-telos-goal-status))
!(test (eval (telos-goal-status "gpu-quota achieved"))
       (updated (goal gpu-quota collective dao achieved) was proposed))
!(test (expression-count-item (eval (telos-report)) (blocked dao-fair-access on gpu-quota)) 0)
!(test (expression-count-item (eval (telos-report)) (achieved-goal gpu-quota dao)) 1)
