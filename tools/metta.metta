;; The metta tool on CeTTa: the code runs in a CeTTa process of its own, after
;; the NAL and PLN libraries, with each metta/NAME.metta loaded into &NAME.
!(import! &self (library proc))
!(import! &self (library str))

(= (description) "Evaluate MeTTa code with CeTTa. Pass multi-line MeTTa code as a string.")
(= (parameters) (code))

(= (metta:absolute $path) (progn (translatePredicate (absolute_file_name $path $absolute)) (text_to_string $absolute)))
(= (metta:import $space $path) (atomics_to_string ("!(import! " $space " \"" (metta:absolute $path) "\")\n")))

(= (metta:imports)
   (atomics_to_string ("!(import! &self (library lib_pln))\n"
                       (if (file-exists "lib_nal.metta") (metta:import "&self" "lib_nal.metta") "")
                       (join-text "" (map-atom (module-files "metta") $path
                                               (metta:import (atomics_to_string ("&" (file-stem $path))) $path))))))

(= (metta:source-file $text)
   (progn (translatePredicate (tmp_file_stream $file $stream ((Predicate (extension metta)) (Predicate (encoding utf8)))))
          (translatePredicate (write $stream $text))
          (translatePredicate (close $stream))
          (text_to_string $file)))

(= (metta:output $out $err)
   (let $output (if (== $err "") $out (str:concat $out (str:concat "\n[STDERR]\n" $err)))
        (if (== (strip $output) "") "(no output)" $output)))

(= (run $code)
   (let $source (strip $code)
        (if (== $source "")
            "No MeTTa code provided"
            (let* (($file (metta:source-file (str:concat (metta:imports) $source)))
                   ($cetta (progn (translatePredicate (read_link "/proc/self/exe" $link $target)) (text_to_string $target)))
                   ($result (proc:run (cons-atom $cetta (cons-atom "--lang" (cons-atom "petta" (cons-atom $file ())))) ((timeout-ms 30000))))
                   ($removed (delete-temp-file $file)))
              (case $result
                    (((proc:result $status proc:timed-out $out $err $capture) "Timeout: MeTTa evaluation took longer than 30 seconds")
                     ((proc:result (proc:launch-failed $errno $stage) $ending $out $err $capture)
                      (atomics_to_string ("Error: could not start CeTTa (" $stage ", errno " $errno ")")))
                     ((proc:result $status $ending $out $err $capture) (metta:output $out $err))))))))
