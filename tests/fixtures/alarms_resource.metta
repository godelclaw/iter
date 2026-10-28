;; Repeated alarm calls in one process, for the harness's resource measurement:
;; each round writes ALARMS_RESOURCE_COUNT alarm files and transforms them, and
;; ALARMS_RESOURCE_FAULT=1 adds a numeric directory that fails every round after
;; the files before it were handled. The output is the resident set (kB) and the
;; open descriptors before the first round and after the last.
!(import! &self "../../utils.metta")
!(import! &self "../../transformations/alarms.metta")
(= (env-number $name $default) (str:string->number (system:getenv-or-default $name $default)))
(= (directory) (system:getenv-or-default "ALARMS_RESOURCE_DIR" ".runtime/alarms-resource"))
(= (descriptors) (size-atom (fs:entries "/proc/self/fd")))
(= (resident)
   (let $status (fs:read-text "/proc/self/status")
     (case (str:find $status "VmRSS:")
       (((TextNotFound $missing) -1)
        ($at (str:string->number (index-atom (str:split-whitespace (index-atom (str:split "\n" (str:byte-slice $status $at (str:byte-length $status))) 0)) 1)))))))
(= (write-alarms $count)
   (if (<= $count 0) done
       (let $w (fs:write (str:join "/" ((directory) (str:number->string $count))) "terminal\nalarm body of some length, repeated for the measurement")
         (write-alarms (- $count 1)))))
(= (rounds $remaining $count $fault)
   (if (<= $remaining 0) done
       (let* (($w (write-alarms $count))
              ($m (if $fault (fs:make-directories (str:join "/" ((directory) "999999999")) 511) ()))
              ($r (alarms:transform-at (JsonArray ()) (JsonArray ()) (directory) 1000000000 "T")))
         (rounds (- $remaining 1) $count $fault))))
!(let* (($made (fs:make-directories (directory) 511))
        ($before (descriptors))
        ($rss-before (resident))
        ($rounds (rounds (env-number "ALARMS_RESOURCE_ROUNDS" "10") (env-number "ALARMS_RESOURCE_COUNT" "10")
                         (== (system:getenv-or-default "ALARMS_RESOURCE_FAULT" "0") "1")))
        ($after (descriptors))
        ($rss-after (resident)))
   (println! (str:join " " ("resident-kb" (str:number->string $rss-before) (str:number->string $rss-after)
                            "descriptors" (str:number->string $before) (str:number->string $after)))))
