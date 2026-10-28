;; Alarm context transformation, as Iter's transformations/alarms.py does.
;;
;; Each file in the alarm directory whose name is a decimal epoch time that has
;; come is read, appended to the messages as a user message and removed. The
;; directory is ITER_ALARMS_DIR, or memory/alarms in the Iter workspace (the
;; Python version's fixed module-relative path is the one deliberate
;; difference); alarms:transform-at takes the directory, the time and the
;; timestamp text directly, for deterministic tests.
;;
;; Alarm text is data throughout: it is never parsed or evaluated. A fault
;; reading, decoding or removing a file is an explicit (Error ...) failure of
;; the call. The alarms already handled stay removed and the failing file stays,
;; as with the Python reference. This is not durable delivery: a file is
;; removed before the caller has accepted the messages, so a failure or an
;; interruption after the removal loses that alarm's message. Nothing here is
;; atomic or exactly-once.
;;
;; Loaded by iter_worker.metta, whose utils.metta provides the libraries.
!(import! &self (library json))
!(import! &self (library str))
!(import! &self (library fs))
!(import! &self (library list))
!(import! &self (library system))
!(import! &self "../lib_text.metta")

(= (description) "Alarm clock handling")

(= (alarms:directory)
   (system:getenv-or-default "ITER_ALARMS_DIR" "memory/alarms"))

;; Python's Path.mkdir default, 0777 narrowed by the umask
(= (alarms:mode) 511)

;; Marks a time or a timestamp that the native clock supplies when it is needed.
(= (alarms:clock) AlarmsWallClockV1)

(= (transform $messages $tools)
   (alarms:transform-with $messages $tools (alarms:directory) (alarms:clock) (alarms:clock)))

;; ($messages $tools) with a user message appended for each alarm whose time
;; $now (a number of epoch seconds, read once) has come, in file name order;
;; $stamp is the text after "Step " in each message. Each handled file is
;; removed. The result is an (Error what why) when the directory cannot be
;; made or read, or an alarm file cannot be read, decoded as UTF-8 or removed.
(= (alarms:transform-at $messages $tools $directory $now $stamp)
   (alarms:transform-with $messages $tools $directory $now $stamp))

(= (alarms:transform-with $messages $tools $directory $when $stamp)
   (case $messages
     (((JsonArray $items)
       (if (alarms:valid-time $when)
           (if (alarms:valid-stamp $stamp)
               (case (fs:make-directories $directory (alarms:mode))
                 ((() (alarms:transform-in $items $messages $tools $directory $when $stamp))
                  ($failed (alarms:fault "cannot make the alarm directory" $failed))))
               (alarms:failure "the timestamp must be a string"))
           (alarms:failure "the time must be a finite number")))
      ($other (alarms:failure "messages must be a JSON array")))))

(= (alarms:valid-time $when)
   (or (== $when (alarms:clock))
       (and (== (get-type $when) Number)
            (== (get-type (json:stringify $when)) String))))
(= (alarms:valid-stamp $stamp)
   (or (== $stamp (alarms:clock)) (== (get-type $stamp) String)))

(= (alarms:transform-in $items $messages $tools $directory $when $stamp)
   (let $now (if (== $when (alarms:clock)) (system:wall-time) $when)
     (case (alarms:due-names $directory $now)
       (((AlarmsDueV1 $names)
         (if (== $names ())
             ($messages $tools)
             (case (alarms:handle-all (alarms:sort $names) $directory $stamp ())
               (((AlarmsHandledV1 $reversed)
                 ((JsonArray (list:append $items (list:reverse $reversed))) $tools))
                ($failure $failure)))))
        ($failure $failure)))))

;; The names in byte order, which is Python's order for file names (code point
;; order of valid UTF-8). A bottom-up merge sort: runs of one name are merged
;; in pairs until one run is left. Every merge is a loop with an accumulator, so
;; the work is n log n and no recursion is as deep as the list is long. The
;; library's recursive list:sort-by was measured to slow sharply, then to need
;; gigabytes, beyond tens of thousands of names. Names are distinct.
(= (alarms:sort $names)
   (alarms:merge-runs (alarms:runs $names ())))

(= (alarms:runs $names $runs)
   (if (== $names ())
       $runs
       (let ($name $rest) (decons-atom $names)
         (let $next (cons-atom ($name) $runs)
           (alarms:runs $rest $next)))))

(= (alarms:merge-runs $runs)
   (if (== $runs ())
       ()
       (let ($first $rest) (decons-atom $runs)
         (if (== $rest ())
             $first
             (alarms:merge-runs (alarms:merge-pass $runs ()))))))

;; One pass: the runs merged two by two.
(= (alarms:merge-pass $runs $merged)
   (if (== $runs ())
       $merged
       (let ($first $rest) (decons-atom $runs)
         (if (== $rest ())
             (cons-atom $first $merged)
             (let ($second $others) (decons-atom $rest)
               (let $run (alarms:merge $first $second ())
                 (let $next (cons-atom $run $merged)
                   (alarms:merge-pass $others $next))))))))

;; $accumulated holds the names already taken, the greatest first.
(= (alarms:merge $left $right $accumulated)
   (if (== $left ())
       (alarms:prepend $accumulated $right)
       (if (== $right ())
           (alarms:prepend $accumulated $left)
           (let ($lower $left-rest) (decons-atom $left)
             (let ($upper $right-rest) (decons-atom $right)
               (if (<= (str:compare $lower $upper) 0)
                   (let $next (cons-atom $lower $accumulated)
                     (alarms:merge $left-rest $right $next))
                   (let $next (cons-atom $upper $accumulated)
                     (alarms:merge $left $right-rest $next))))))))

;; The reversed $accumulated put in front of $rest.
(= (alarms:prepend $accumulated $rest)
   (if (== $accumulated ())
       $rest
       (let ($head $tail) (decons-atom $accumulated)
         (let $next (cons-atom $head $rest)
           (alarms:prepend $tail $next)))))

;; (AlarmsDueV1 names) of the entries named by a number that $now has reached,
;; or the failure. The directory owner is closed on every path.
(= (alarms:due-names $directory $now)
   (case (fs:open-directory $directory)
     (((fs:failed $reason $operation $path $code $progress $message)
       (alarms:fault "cannot read the alarm directory"
                     (fs:failed $reason $operation $path $code $progress $message)))
      ($owner (alarms:drain $owner $now ())))))

(= (alarms:drain $owner $now $due)
   (case (fs:next-entry $owner)
     (((fs:entry $name $hint $index)
       (if (alarms:due $name $now)
           (alarms:drain $owner $now (cons-atom $name $due))
           (alarms:drain $owner $now $due)))
      ((fs:end $count)
       (case (fs:close $owner)
         ((fs:closed (AlarmsDueV1 $due))
          ($failed (alarms:fault "cannot close the alarm directory" $failed)))))
      ($failed
       (let $closed (fs:close $owner)
         (alarms:fault "cannot list the alarm directory" $failed))))))

;; Whether the name is a whole decimal number that $now has reached.
(= (alarms:due $name $now)
   (case (str:string->number $name)
     (((Error $call $why) False)
      ($target (alarms:at-or-after $now (* 1.0 $target))))))

;; The reference parses deadlines as binary64, including integer spellings.
;; Injected clocks may be exact integers. Compare their integer floors first
;; so a mixed comparison cannot round an integer clock up to a future deadline.
;; Within the same unit interval, fractional binary64 comparisons are exact
;; for this purpose; above 2^53 the represented floats have no fractional part.
(= (alarms:at-or-after $now $target)
   (let* (($now-floor (floor-math $now))
          ($target-floor (floor-math $target)))
     (if (== $now-floor $target-floor)
         (>= $now $target)
         (> $now-floor $target-floor))))

;; (AlarmsHandledV1 reversed-messages) after reading, removing and appending
;; each alarm in turn, or the failure of the first that fails.
(= (alarms:handle-all $names $directory $stamp $reversed)
   (if (== $names ())
       (AlarmsHandledV1 $reversed)
       (let ($name $rest) (decons-atom $names)
         (case (alarms:handle (alarms:path $directory $name) $stamp)
           (((AlarmsMessageV1 $message)
             (let $next (cons-atom $message $reversed)
               (alarms:handle-all $rest $directory $stamp $next)))
            ($failure $failure))))))

(= (alarms:path $directory $name)
   (if (str:ends-with $directory "/")
       (str:concat $directory $name)
       (str:join "/" ($directory $name))))

;; (AlarmsMessageV1 message) for one file, which is removed, or the failure.
(= (alarms:handle $path $stamp)
   (case (fs:read-text-result $path 0 "raw")
     ((("ok" $raw)
       (let $decoder (str:utf8-new strict)
         (case (str:utf8-feed $decoder $raw true)
           (((Utf8DecodedV1 $text $next) (alarms:deliver $path $text $stamp))
            ((StrRefusedV1 $call (StrMalformedUtf8V1 $offset))
             (alarms:failure (str:join "" ("cannot decode alarm " $path
                                           " as UTF-8: malformed at byte "
                                           (str:number->string $offset)))))
            ($refused (alarms:failure (str:join "" ("cannot decode alarm " $path " as UTF-8"))))))))
      (("directory" $detail)
       (alarms:failure (str:join "" ("cannot read alarm " $path ": it is a directory"))))
      (("denied" $detail)
       (alarms:failure (str:join "" ("cannot read alarm " $path ": permission denied"))))
      (("missing" $detail)
       (alarms:failure (str:join "" ("cannot read alarm " $path ": it is missing"))))
      ($refused
       (alarms:failure (str:join "" ("cannot read alarm " $path)))))))

;; The channel is the first line of the stripped text when it has more, else
;; terminal; the message is the rest, or all of the text.
(= (alarms:deliver $path $text $stamp)
   (let* (($content (text:strip (text:universal-newlines $text)))
          ($cut (str:find $content "\n"))
          ($when (alarms:stamp-text $stamp)))
     (if (== (get-type $when) String)
         (case $cut
           (((TextNotFound $missing) (alarms:remove-and-append $path "terminal" $content $when))
            ($offset (alarms:remove-and-append $path
                       (str:byte-slice $content 0 $offset)
                       (str:byte-slice $content (+ $offset 1) (str:byte-length $content))
                       $when))))
         (alarms:failure "cannot format the alarm time"))))

(= (alarms:stamp-text $stamp)
   (if (== $stamp (alarms:clock))
       (system:format-time "%Y-%m-%d %H:%M:%S" (system:wall-time) local)
       $stamp))

(= (alarms:remove-and-append $path $channel $message $when)
   (case (fs:remove $path)
     ((() (AlarmsMessageV1
            (JsonObject ((JsonPair "role" "user")
                         (JsonPair "content"
                           (str:join "" ("Step " $when ": [" $channel "] [ALARM] " $message)))))))
      ($failed (alarms:fault (str:concat "cannot remove alarm " $path) $failed)))))

(= (alarms:failure $why) (Error "alarms" $why))

;; The failure of a native fs call, as text.
(= (alarms:fault $what $failed)
   (case $failed
     (((fs:failed $reason $operation $path $code $progress $message)
       (alarms:failure (str:join "" ($what ": " $message " (" $operation " " $path ")"))))
      ($other (alarms:failure $what)))))
