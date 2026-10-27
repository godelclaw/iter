;; The metta tool on CeTTa: the code runs inline in a CeTTa process of its own,
;; after the NAL and PLN libraries, with each metta/NAME.metta loaded into
;; &NAME. That process belongs to this worker and ends with it.
(= (description) "Evaluate MeTTa code with CeTTa. Pass multi-line MeTTa code as a string.")
(= (parameters) (code))

(= (metta:import $space $path)
   (str-cat ("!(import! " $space " \"" (str:escape metta (fs:canonical-path $path)) "\")\n")))

(= (metta:imports)
   (str-cat ("!(import! &self (library lib_pln))\n"
             (if (fs:is-file "lib_nal.metta") (metta:import "&self" "lib_nal.metta") "")
             (str:join "" (map-atom (module-files "metta") $path (metta:import (str:concat "&" (module-name $path)) $path))))))

(= (metta:output $out $err)
   (let $output (if (== $err "") $out (str:concat $out (str:concat "\n[STDERR]\n" $err)))
        (if (== (str:trim $output) "") "(no output)" $output)))

(= (run $code)
   (let $source (str:trim $code)
        (if (== $source "")
            "No MeTTa code provided"
            (case (proc:run ((cetta) "--lang" "petta" "-e" (str:concat (metta:imports) $source)) ((timeout-ms 30000) (lifetime owner)))
              (((proc:result $termination proc:timed-out $out $err $capture) "Timeout: MeTTa evaluation took longer than 30 seconds")
               ((proc:result (proc:launch-failed $errno $stage) $cause $out $err $capture)
                (str-cat ("Error: could not start CeTTa (" $stage ", errno " $errno ")")))
               ((proc:result $termination $cause $out $err $capture) (metta:output $out $err))
               ((proc:failed $kind $reason) (str:concat "Error: " $reason)))))))
