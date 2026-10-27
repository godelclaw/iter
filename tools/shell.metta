;; The shell tool: sh -c with output and error captured, as Python's
;; subprocess.run(cmd, shell=True, capture_output=True) did.
!(import! &self (library proc))
!(import! &self (library str))

(= (description) "Execute a shell command.")
(= (parameters) (cmd))

(= (run $cmd)
   (case (proc:run (cons-atom "sh" (cons-atom "-c" (cons-atom $cmd ()))) ())
         (((proc:result (proc:launch-failed $errno $stage) $ending $out $err $capture)
           (Error sh (atomics_to_string ("could not start sh (" $stage ", errno " $errno ")"))))
          ((proc:result $status $ending $out $err $capture)
           (str:concat "SUCCESS, RETURN: " (str:concat $out $err))))))
