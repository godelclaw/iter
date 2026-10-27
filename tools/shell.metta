;; The shell tool: sh -c with output and error captured, as Python's
;; subprocess.run(cmd, shell=True, capture_output=True) did. The command's
;; processes, background ones included, end with it, or with this worker when
;; a timeout kills the worker first.
(= (description) "Execute a shell command.")
(= (parameters) (cmd))

(= (run $cmd)
   (case (proc:run ("sh" "-c" $cmd) ((lifetime owner)))
     (((proc:result (proc:launch-failed $errno $stage) $cause $out $err $capture)
       (Error sh (str-cat ("could not start sh (" $stage ", errno " $errno ")"))))
      ((proc:result $termination $cause $out $err $capture)
       (str:concat "SUCCESS, RETURN: " (str:concat $out $err)))
      ((proc:failed $kind $reason) (Error sh $reason)))))
