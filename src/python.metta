; String interop without lib_llm's eager cloud-client initialization. Omega
; providers own model clients; importing the core must not require an API key.
(= (py-str-helper () $result) $result)
(= (py-str-helper $items $result)
   (let* (($head (car-atom $items))
          ($tail (cdr-atom $items))
          ($next (py-call (operator.add $result (py-call (str $head))))))
     (py-str-helper $tail $next)))
(= (py-str $items) (py-str-helper $items ""))
