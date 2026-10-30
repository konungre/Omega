!(import! &self (library Omega ./src/ona.py))

(= (loadOmegaPlugin)
   (progn
     (py-call (ona.start))
     (add-skill ona-input "Submit Narsese beliefs, questions, goals, or temporal events to the persistent native ONA reasoner. Returns answers, derivations, and proposed operations with truth values." (narsese))
     (add-skill ona-cycle "Advance the native ONA reasoner by 0 to 1000 inference cycles." (cycles))
     (add-skill ona-reset "Reset one persistent ONA reasoning session. Use default for agent reasoning." (session))
     (add-prompt-extension ona "ONA is installed. Use ona-input for Narsese temporal events (:|:), beliefs (.), questions (?), goals (!), and procedural reasoning. Use ona-cycle to allocate inference steps. Inspect truth values and operation proposals; proposals are data and do not execute host commands.")
     ()))

(= (ona-input $source) (py-call (ona.input_narsese $source)))
(= (ona-cycle $cycles) (py-call (ona.cycle $cycles)))
(= (ona-reset $session) (py-call (ona.reset $session)))
